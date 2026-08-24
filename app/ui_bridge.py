"""Thin, read-only projection from canonical review artifacts to the local UI."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from app.cli import initial_state
from app.orchestration.reporting import ReportArtifact
from app.orchestration.runtime import ReviewRequestContext
from app.runtime.local import compose_local_runtime, load_dotenv

_VERDICT_VIEW = {
    "support": ("verified", "근거가 주장을 뒷받침합니다."),
    "partial_support": ("partial", "일부 근거가 확인됐지만 불확실성이 남아 있습니다."),
    "contradicted": ("partial", "상충하는 근거가 확인됐습니다."),
    "unsupported": ("unverified", "주장을 뒷받침할 근거가 확인되지 않았습니다."),
    "unverifiable": ("unverified", "현재 근거로는 검증할 수 없습니다."),
}


def public_error(_error: BaseException) -> dict[str, str]:
    """Return a fixed public error without reflecting internal exception data."""

    return {
        "code": "REVIEW_FAILED",
        "message": "검토를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }


async def build_result_view(runtime: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Project persisted canonical artifacts without adding a new judgment."""

    review_store = runtime.deps.review_store
    evidence_store = runtime.deps.evidence_store
    claims = await review_store.get_claims(list(state.get("claim_ids", [])))
    evaluations = await review_store.get_claim_evaluations(
        list(state.get("claim_evaluation_ids", []))
    )
    evaluations_by_claim = {str(item.claim_id): item for item in evaluations}

    claim_views: list[dict[str, str]] = []
    evidence_ids: list[str] = []
    for claim in claims:
        evaluation = evaluations_by_claim.get(str(claim.claim_id))
        if evaluation is None:
            continue
        verdict = evaluation.verdict
        status, summary = _VERDICT_VIEW[verdict]
        claim_views.append(
            {
                "text": claim.normalized_proposition,
                "status": status,
                "summary": summary,
            }
        )
        evidence_ids.extend(str(item.evidence_id) for item in evaluation.citations)

    unique_evidence_ids = list(dict.fromkeys(evidence_ids))
    evidence = await evidence_store.get_many(unique_evidence_ids)
    evidence_views = [
        {
            "source": item.publisher or item.source_type.upper(),
            "excerpt": item.raw_span,
            "url": item.source_url,
            "publishedAt": item.published_at.isoformat() if item.published_at else None,
        }
        for item in evidence
    ]

    report_body = await review_store.get_report(state["report_id"])
    report = ReportArtifact.model_validate(report_body)
    stock = state.get("stock") or {}
    degraded = bool(report.banners)

    return {
        "stock": {"code": stock.get("code"), "name": stock.get("name")},
        "claims": claim_views,
        "evidence": evidence_views,
        "finalSummary": "\n\n".join(slot.text for slot in report.rendered_slots),
        "banners": list(report.banners),
        "degraded": degraded,
    }


async def serve(read_message: Any, emit: Any, *, runtime: Any) -> None:
    """Serve exactly one review session over an injected JSON-message boundary."""

    try:
        message = await read_message()
        if message.get("kind") != "start" or not isinstance(message.get("text"), str):
            raise ValueError("first message must be start")

        run_id = f"ui-{uuid4().hex}"
        config = {"configurable": {"thread_id": run_id}}
        result = await runtime.graph.ainvoke(
            initial_state(run_id, run_id, now=datetime.now(UTC)),
            config,
            context=ReviewRequestContext(raw_text=message["text"]),
        )

        for _ in range(10):
            interrupts = result.get("__interrupt__")
            if not interrupts:
                await emit({"kind": "result", "result": await build_result_view(runtime, result)})
                return
            await emit({"kind": "hitl", "payload": interrupts[0].value})
            message = await read_message()
            if message.get("kind") != "resume" or "value" not in message:
                raise ValueError("expected resume message")
            result = await runtime.graph.ainvoke(Command(resume=message["value"]), config)
        raise RuntimeError("HITL turn limit exceeded")
    except BaseException as error:
        await emit({"kind": "error", **public_error(error)})


async def _stdio_main() -> None:
    load_dotenv()

    async def read_message() -> dict[str, Any]:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            raise EOFError("worker input closed")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("message must be an object")
        return value

    async def emit(message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()

    try:
        async with compose_local_runtime() as runtime:
            await serve(read_message, emit, runtime=runtime)
    except BaseException as error:
        await emit({"kind": "error", **public_error(error)})


if __name__ == "__main__":
    asyncio.run(_stdio_main())
