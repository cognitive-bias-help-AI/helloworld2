"""Thin, read-only projection from canonical review artifacts to the local UI."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.cli import initial_state
from app.diagnostics import debug_enabled, debug_log
from app.domain.intake import (
    FreeTextInput,
    HybridIntake,
    IntakeMode,
    ResponseState,
    StructuredAnswer,
)
from app.orchestration.reporting import ReportArtifact
from app.orchestration.runtime import ReviewRequestContext
from app.runtime.local import compose_local_runtime, load_dotenv
from app.schemas.frozen import NonBlankStr, SourceTrace

_VERDICT_VIEW = {
    "support": ("verified", "근거가 주장을 뒷받침합니다."),
    "partial_support": ("partial", "일부 근거가 확인됐지만 불확실성이 남아 있습니다."),
    "contradicted": ("partial", "상충하는 근거가 확인됐습니다."),
    "unsupported": ("unverified", "주장을 뒷받침할 근거가 확인되지 않았습니다."),
    "unverifiable": ("unverified", "현재 근거로는 검증할 수 없습니다."),
}


class StructuredIntakeRequest(BaseModel):
    """Closed UI transport DTO for an explicit HybridIntake survey."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stock_input: NonBlankStr = Field(validation_alias="stockInput")
    decision_action: Literal["CONSIDER_ENTRY", "HOLD", "CONSIDER_EXIT", "WAIT"] = (
        Field(validation_alias="decisionAction")
    )
    holding_state: Literal["HOLDING", "NOT_HOLDING"] = Field(
        validation_alias="holdingState"
    )
    time_horizon: Literal["SHORT", "MEDIUM", "LONG", "UNDECIDED"] = Field(
        validation_alias="timeHorizon"
    )
    primary_reasons: NonBlankStr = Field(validation_alias="primaryReasons")
    expected_outcome: NonBlankStr | None = Field(
        default=None, validation_alias="expectedOutcome"
    )
    information_checked: (
        tuple[
            Literal[
                "FINANCIALS",
                "DISCLOSURE",
                "NEWS",
                "PRICE_CHART",
                "INDUSTRY",
                "OTHER",
                "NONE_CHECKED",
            ],
            ...,
        ]
        | None
    ) = Field(default=None, validation_alias="informationChecked")
    counter_evidence_concerns: NonBlankStr | None = Field(
        default=None, validation_alias="counterEvidenceConcerns"
    )
    change_conditions: NonBlankStr | None = Field(
        default=None, validation_alias="changeConditions"
    )

    @model_validator(mode="after")
    def reject_explicit_null_optionals(self):
        for field_name in (
            "expected_outcome",
            "information_checked",
            "counter_evidence_concerns",
            "change_conditions",
        ):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} must be omitted instead of null")
        if self.information_checked is not None:
            if len(set(self.information_checked)) != len(self.information_checked):
                raise ValueError("information_checked must not repeat values")
            if "NONE_CHECKED" in self.information_checked and len(self.information_checked) != 1:
                raise ValueError("NONE_CHECKED must be selected alone")
        return self


def _survey_answer(slot_id: int, value: str | tuple[str, ...]) -> StructuredAnswer:
    return StructuredAnswer(
        slot_id=slot_id,
        value=value,
        source=SourceTrace.SURVEY,
        response_state=ResponseState.ANSWERED,
    )


def _project_structured_intake(payload: object) -> HybridIntake:
    if not isinstance(payload, dict):
        raise ValueError("structured intake must be an object")
    request = StructuredIntakeRequest.model_validate(payload)
    structured = [
        _survey_answer(1, request.decision_action),
        _survey_answer(2, request.holding_state),
        _survey_answer(3, request.time_horizon),
        _survey_answer(4, request.primary_reasons),
    ]
    for slot_id, value in (
        (5, request.expected_outcome),
        (6, request.information_checked),
        (7, request.counter_evidence_concerns),
        (8, request.change_conditions),
    ):
        if value is not None:
            structured.append(_survey_answer(slot_id, value))
    return HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.HYBRID,
        structured=tuple(structured),
        free_text=(
            FreeTextInput(
                text=request.stock_input,
                source=SourceTrace.CHAT_EXPLICIT,
            ),
        ),
    )


def _start_context(message: object) -> ReviewRequestContext:
    if not isinstance(message, dict) or message.get("kind") != "start":
        raise ValueError("first message must be start")
    if "intake" in message:
        if set(message) != {"kind", "intake"}:
            raise ValueError("structured start contains undeclared fields")
        return ReviewRequestContext(intake=_project_structured_intake(message["intake"]))
    if set(message) != {"kind", "text"} or not isinstance(message.get("text"), str):
        raise ValueError("legacy start must carry text only")
    return ReviewRequestContext(raw_text=message["text"])


def public_error(_error: BaseException) -> dict[str, str]:
    """Return a fixed public error without reflecting internal exception data."""

    return {
        "code": "REVIEW_FAILED",
        "message": "검토를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    }


_JUDGMENT_CONTEXT_SLOTS = {
    1: "decisionAction",
    2: "holdingState",
    3: "timeHorizon",
    4: "primaryReasons",
    5: "expectedOutcome",
}


def _project_judgment_context(input_body: object) -> dict[str, str]:
    """Expose only the approved survey slots from persisted masked intake."""

    if not isinstance(input_body, dict):
        return {}
    masked_intake = input_body.get("masked_intake")
    if not isinstance(masked_intake, dict):
        return {}
    structured = masked_intake.get("structured")
    if not isinstance(structured, list):
        return {}

    context: dict[str, str] = {}
    for answer in structured:
        if not isinstance(answer, dict):
            continue
        field = _JUDGMENT_CONTEXT_SLOTS.get(answer.get("slot_id"))
        value = answer.get("value")
        if field is not None and isinstance(value, str):
            context[field] = value
    return context


async def build_result_view(runtime: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Project persisted canonical artifacts without adding a new judgment."""

    review_store = runtime.deps.review_store
    node_results = state.get("node_results", [])
    report_id = state.get("report_id")
    if not report_id:
        if any(isinstance(item, str) and ":block:" in item for item in node_results):
            raise RuntimeError("review terminated without report")
        raise RuntimeError("published report is missing")
    report_body = await review_store.get_report(report_id)
    if report_body is None:
        raise RuntimeError("published report is missing")
    report = ReportArtifact.model_validate(report_body)

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

    stock = state.get("stock") or {}
    degraded = bool(report.banners)
    input_id = state.get("input_id")
    judgment_context = {}
    if isinstance(input_id, str) and input_id:
        judgment_context = _project_judgment_context(await review_store.get_input(input_id))

    return {
        "stock": {"code": stock.get("code"), "name": stock.get("name")},
        "claims": claim_views,
        "evidence": evidence_views,
        "finalSummary": "\n\n".join(slot.text for slot in report.rendered_slots),
        "banners": list(report.banners),
        "degraded": degraded,
        "judgmentContext": judgment_context,
    }


async def serve(read_message: Any, emit: Any, *, runtime: Any) -> None:
    """Serve exactly one review session over an injected JSON-message boundary."""

    run_id: str | None = None
    command = "waiting"
    phase = "receive"
    try:
        message = await read_message()
        command = str(message.get("kind")) if isinstance(message, dict) else "invalid"
        debug_log("review", "received command", command=command)
        context = _start_context(message)

        run_id = f"ui-{uuid4().hex}"
        config = {"configurable": {"thread_id": run_id}}
        phase = "graph"
        debug_log("review", "graph invocation started", run_id=run_id, thread_id=run_id)
        result = await runtime.graph.ainvoke(
            initial_state(run_id, run_id, now=datetime.now(UTC)),
            config,
            context=context,
        )
        debug_log("review", "graph invocation returned", run_id=run_id)

        for _ in range(10):
            interrupts = result.get("__interrupt__")
            if not interrupts:
                phase = "result_projection"
                debug_log("review", "result projection started", run_id=run_id)
                await emit({"kind": "result", "result": await build_result_view(runtime, result)})
                debug_log("review", "result projection completed", run_id=run_id)
                return
            payload = interrupts[0].value
            details: dict[str, Any] = {}
            if isinstance(payload, dict):
                details = {
                    "schema": payload.get("schema_version"),
                    "candidate_count": len(payload.get("candidates", ())),
                    "question_count": len(payload.get("questions", ())),
                    "ask_ids": [q.get("ask_id") for q in payload.get("questions", ()) if isinstance(q, dict)],
                    "slot_ids": [q.get("slot_id") for q in payload.get("questions", ()) if isinstance(q, dict)],
                }
            debug_log("hitl", "interrupt", run_id=run_id, **details)
            await emit({"kind": "hitl", "payload": payload})
            debug_log("review", "HITL emitted", run_id=run_id)
            message = await read_message()
            command = str(message.get("kind")) if isinstance(message, dict) else "invalid"
            debug_log("review", "received command", run_id=run_id, command=command)
            if message.get("kind") != "resume" or "value" not in message:
                raise ValueError("expected resume message")
            resume = message["value"]
            resume_fields = list(resume) if isinstance(resume, dict) else []
            debug_log(
                "hitl", "resume", run_id=run_id, fields=resume_fields,
                selected_code=resume.get("selected_code") if isinstance(resume, dict) else None,
                answer_count=len(resume.get("answers", ())) if isinstance(resume, dict) else None,
            )
            phase = "graph_resume"
            debug_log("review", "graph invocation started", run_id=run_id, command="resume")
            result = await runtime.graph.ainvoke(Command(resume=message["value"]), config)
            debug_log("review", "graph invocation returned", run_id=run_id, command="resume")
        raise RuntimeError("HITL turn limit exceeded")
    except BaseException as error:
        if debug_enabled():
            print("=" * 40, file=sys.stderr, flush=True)
            print("REVIEW WORKER FAILURE", file=sys.stderr, flush=True)
            print("=" * 40, file=sys.stderr, flush=True)
            debug_log(
                "review", "failure", run_id=run_id, thread_id=run_id, phase=phase,
                command=command, exception_type=type(error).__name__,
                exception_message=str(error),
            )
            traceback.print_exc(file=sys.stderr)
            print("=" * 40, file=sys.stderr, flush=True)
        await emit({"kind": "error", **public_error(error)})


async def _stdio_main() -> None:
    debug_log("review", "worker started")
    load_dotenv()
    debug_log("review", "environment loaded")
    if debug_enabled():
        names = (
            "MODEL_BACKEND", "ANTHROPIC_API_KEY", "LUNA_API_URL", "LUNA_API_KEY",
            "TERRA_API_URL", "TERRA_API_KEY", "SOL_API_URL", "SOL_API_KEY",
            "DART_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
            "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "DATABASE_URL",
        )
        debug_log("config", "presence", **{name: "SET" if os.getenv(name) else "UNSET" for name in names})

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
        debug_log("review", "runtime composition started")
        async with compose_local_runtime() as runtime:
            debug_log("review", "runtime composition completed")
            await serve(read_message, emit, runtime=runtime)
    except BaseException as error:
        if debug_enabled():
            traceback.print_exc(file=sys.stderr)
        await emit({"kind": "error", **public_error(error)})
    finally:
        debug_log("review", "worker terminating")


if __name__ == "__main__":
    asyncio.run(_stdio_main())
