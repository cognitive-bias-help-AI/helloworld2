"""The fourteen thin S0 runtime vertices."""

from __future__ import annotations

import re
from hashlib import sha256

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.assemblers.claim_evaluation import assemble_claim_evaluation
from app.assemblers.claim_evidence import assemble_claim_evidence
from app.assemblers.errors import AssemblyError
from app.assemblers.fallbacks import (
    assemble_unknown_claim_evidence_fallback,
    assemble_unverifiable_evaluation_fallback,
    omit_invalid_findings_fallback,
)
from app.assemblers.findings import assemble_findings
from app.contexts.budget import NODE_BUDGETS, ctx_chars, ctx_items
from app.contexts.views import (
    AskBackContext,
    ClaimView,
    ClassifiedEvidenceView,
    EvidenceExcerptView,
    EvidencePacket,
    GuardBatchEnvelope,
    GuardScanView,
    IntegrationView,
    MissingSlotView,
    RenderCitationView,
    RenderView,
    SlotContext,
    SlotDefinitionView,
    SlotTextView,
    VerifyPacket,
)
from app.gateway.assemble import assemble_evidence
from app.orchestration.drafts import (
    AskBackDraft,
    FindingDraft,
    GuardScanResult,
    GuardVerdictDraft,
    RenderDraft,
    SlotExtractionDraft,
)
from app.orchestration.hitl import StockChoiceRequest, StockChoiceResume, select_stock
from app.orchestration.limits import REWRITE_LIMIT
from app.orchestration.reporting import build_report_artifact
from app.orchestration.runtime import ReviewRequestContext, RuntimeDeps
from app.orchestration.state import ReviewState
from app.orchestration.validators.citations import validate_citations
from app.schemas.frozen import (
    Claim,
    ClaimEvaluationDraft,
    ClaimStanceDraft,
    GuardInput,
    NodeStatus,
    OpposeBlock,
    ProviderCall,
    Query,
    ReasonCode,
    SourceTrace,
)


def _mask(value: str) -> str:
    value = " ".join(value.split())
    value = re.sub(r"[\w.+-]+@[\w.-]+", "[EMAIL]", value)
    return re.sub(r"\b01[016789]-?\d{3,4}-?\d{4}\b", "[PHONE]", value)


def _budget(node: str, view) -> None:
    limit = NODE_BUDGETS[node]
    if ctx_chars(view) > limit.chars or (limit.items is not None and ctx_items(view) > limit.items):
        raise RuntimeError(ReasonCode.BUDGET_EXCEEDED.value)


async def _invoke(deps: RuntimeDeps, node: str, slot: str, view, schema):
    _budget(node, view)
    return await deps.model_gateway.invoke(slot, f"{node}/v1", view, schema)


def make_nodes(deps: RuntimeDeps):
    async def n0(state: ReviewState, runtime: Runtime[ReviewRequestContext]):
        if runtime.context is None or not runtime.context.raw_text.strip():
            raise ValueError("ReviewRequestContext.raw_text is required")
        masked = _mask(runtime.context.raw_text)
        input_id = await deps.review_store.put_input(state["run_id"], {"masked_input": masked})
        return {"input_id": input_id, "node_results": ["n0:ok"]}

    async def n1(state: ReviewState):
        body = await deps.review_store.get_input(state["input_id"])
        result, _ = await _invoke(deps, "n1", "SMALL", GuardScanView(**body), GuardScanResult)
        suffix = "ok" if result.reason_code is None else f"block:{result.reason_code.value}"
        return {"node_results": [f"n1:{suffix}"], "counters": {"llm_calls": 1}}

    async def n2(state: ReviewState):
        body = await deps.review_store.get_input(state["input_id"])
        candidates = deps.stock_resolver.resolve(body["masked_input"])
        if not candidates:
            return {"node_results": [f"n2:block:{ReasonCode.STOCK_UNRESOLVED.value}"]}
        resume = None
        if len(candidates) > 1:
            payload = interrupt(
                StockChoiceRequest.from_candidates(body["masked_input"], candidates).model_dump()
            )
            resume = StockChoiceResume.model_validate(payload)
        selected = select_stock(candidates, resume)
        return {"stock": selected.model_dump(), "node_results": ["n2:ok"]}

    async def n3(state: ReviewState):
        body = await deps.review_store.get_input(state["input_id"])
        view = SlotContext(
            masked_input=body["masked_input"],
            slot_definitions=[
                SlotDefinitionView(slot_id=1, name="투자 주장", description="검증할 주장")
            ],
        )
        draft, _ = await _invoke(deps, "n3", "SMALL", view, SlotExtractionDraft)
        now = deps.clock()
        claims = [
            Claim(
                claim_id=deps.id_factory(),
                **item.model_dump(),
                origin=SourceTrace.LLM_EXTRACTION,
                created_at=now,
            )
            for item in draft.claims
        ]
        ids = await deps.review_store.put_claims(state["run_id"], claims)
        slots = [{"slot_id": item.slot_id, "status": "filled"} for item in draft.claims]
        return {
            "claim_ids": ids,
            "slots": slots,
            "node_results": ["n3:ok"],
            "counters": {"llm_calls": 1},
        }

    async def n4(state: ReviewState):
        view = AskBackContext(
            missing_slots=[MissingSlotView(slot_id=1, status="absent", summary="투자 주장 없음")]
        )
        draft, _ = await _invoke(deps, "n4", "SMALL", view, AskBackDraft)
        answer = interrupt({"questions": [item.model_dump() for item in draft.questions]})
        return {
            "user_action": answer,
            "node_results": ["n4:resume"],
            "counters": {"llm_calls": 1, "hitl_reask": 1},
        }

    async def n3b(state: ReviewState):
        answer = state.get("user_action") or {}
        text = str(answer.get("answer", "")).strip()
        if not text:
            return {"node_results": [f"n3b:block:{ReasonCode.INPUT_INSUFFICIENT.value}"]}
        claim = Claim(
            claim_id=deps.id_factory(),
            slot_id=1,
            user_text_span=text,
            span_offset=(0, len(text)),
            normalized_proposition=text,
            verifiable=True,
            origin=SourceTrace.USER_CONFIRMED,
            created_at=deps.clock(),
        )
        ids = await deps.review_store.put_claims(state["run_id"], [claim])
        return {
            "claim_ids": ids,
            "slots": [{"slot_id": 1, "status": "filled"}],
            "node_results": ["n3b:ok"],
        }

    async def n5(state: ReviewState):
        queries = [
            Query(
                query_id=deps.id_factory(),
                scope="claim",
                claim_id=claim_id,
                intent="verify",
                provider="dart",
                endpoint="disclosure",
                params={"stock_code": state["stock"]["code"]},
                created_at=deps.clock(),
            )
            for claim_id in state["claim_ids"]
        ]
        ids = await deps.evidence_store.put_queries(state["run_id"], queries)
        return {"query_ids": ids, "node_results": ["n5:ok"]}

    async def n6(state: ReviewState):
        queries = await deps.evidence_store.get_queries(state["query_ids"])
        adopted = 0
        for query in queries:
            adapter = deps.adapters[query.provider]
            request = adapter.build_request(query, deps.clock())
            raw = await adapter.acall(request)
            drafts = adapter.parse_response(raw, query)
            call = ProviderCall(
                provider_request_id=deps.id_factory(),
                run_id=state["run_id"],
                provider=query.provider,
                endpoint=query.endpoint,
                query_id=query.query_id,
                latency_ms=0,
                idempotency_key=sha256(f"{state['run_id']}|{query.query_id}".encode()).hexdigest(),
                created_at=deps.clock(),
            )
            evidence, _ = await assemble_evidence(
                drafts,
                query,
                call,
                deps.clock(),
                state["run_id"],
                deps.clock(),
                deps.evidence_store,
            )
            adopted += len(evidence)
        return {
            "collections": {"dart": {"status": NodeStatus.OK.value, "items_adopted": adopted}},
            "node_results": ["n6:ok"],
            "counters": {"external_calls": len(queries)},
        }

    async def n7(state: ReviewState):
        claims = await deps.review_store.get_claims(state["claim_ids"])
        for claim in claims:
            evidence_ids = await deps.evidence_store.evidence_ids_for_claim(claim.claim_id)
            evidence = await deps.evidence_store.get_many(evidence_ids)
            view = EvidencePacket(
                claim=ClaimView(
                    claim_id=claim.claim_id,
                    slot_id=claim.slot_id,
                    normalized_proposition=claim.normalized_proposition,
                ),
                evidence=[
                    EvidenceExcerptView(
                        **item.model_dump(include=set(EvidenceExcerptView.model_fields))
                    )
                    for item in evidence
                ],
            )
            draft = None
            for _ in range(2):
                candidate, _ = await _invoke(deps, "n7", "SMALL", view, ClaimStanceDraft)
                try:
                    mapping = {eid: state["query_ids"][0] for eid in evidence_ids}
                    items = assemble_claim_evidence(
                        candidate, claim.claim_id, evidence_ids, mapping
                    )
                    draft = items
                    break
                except AssemblyError as exc:
                    if not exc.retryable:
                        raise
            if draft is None:
                draft = assemble_unknown_claim_evidence_fallback(
                    claim.claim_id, evidence_ids, mapping
                )
            await deps.review_store.put_claim_evidence(state["run_id"], draft)
        degraded = any(item.stance_source == "rule" for item in draft)
        return {
            "node_results": ["n7:partial" if degraded else "n7:ok"],
            "counters": {"llm_calls": 2 if degraded else 1},
        }

    async def n8(state: ReviewState):
        evaluations = []
        for claim in await deps.review_store.get_claims(state["claim_ids"]):
            evidence_ids = await deps.evidence_store.evidence_ids_for_claim(claim.claim_id)
            evidence = await deps.evidence_store.get_many(evidence_ids)
            links = await deps.review_store.get_claim_evidence(state["run_id"], claim.claim_id)
            stance = {item.evidence_id: item.stance for item in links}
            view = VerifyPacket(
                claim=ClaimView(
                    claim_id=claim.claim_id,
                    slot_id=claim.slot_id,
                    normalized_proposition=claim.normalized_proposition,
                ),
                evidence=[
                    ClassifiedEvidenceView(
                        **item.model_dump(include=set(EvidenceExcerptView.model_fields)),
                        stance=stance[item.evidence_id],
                    )
                    for item in evidence
                ],
                numeric_checks=[],
            )
            assembled = None
            for _ in range(2):
                candidate, _ = await _invoke(deps, "n8", "LARGE", view, ClaimEvaluationDraft)
                try:
                    assembled = assemble_claim_evaluation(
                        candidate, claim.claim_id, evidence_ids, [], deps.id_factory(), deps.clock()
                    )
                    break
                except AssemblyError as exc:
                    if not exc.retryable:
                        raise
            if assembled is None:
                assembled = assemble_unverifiable_evaluation_fallback(
                    claim_id=claim.claim_id,
                    packet_evidence_ids=evidence_ids,
                    numeric_checks=[],
                    claim_evaluation_id=deps.id_factory(),
                    created_at=deps.clock(),
                )
            evaluations.append(assembled)
        ids = await deps.review_store.put_claim_evaluations(state["run_id"], evaluations)
        degraded = any(
            ReasonCode.COVERAGE_TRUNCATED in item.uncertainty_codes for item in evaluations
        )
        return {
            "claim_evaluation_ids": ids,
            "node_results": ["n8:partial" if degraded else "n8:ok"],
            "counters": {"llm_calls": 2 if degraded else 1},
        }

    async def n9(state: ReviewState):
        evaluations = await deps.review_store.get_claim_evaluations(state["claim_evaluation_ids"])
        view = IntegrationView(
            evaluations=evaluations,
            oppose=OpposeBlock(status="verified", count=0, queries=["반대 근거 검색"]),
            missing_slots=[],
        )
        drafts = None
        for _ in range(2):
            candidate, _ = await _invoke(deps, "n9", "LARGE", view, FindingDraft)
            values = [candidate]
            try:
                drafts = assemble_findings(values, evaluations, [deps.id_factory()], deps.clock())
                break
            except AssemblyError as exc:
                if not exc.retryable:
                    raise
        if drafts is None:
            drafts = omit_invalid_findings_fallback()
        ids = await deps.review_store.put_findings(state["run_id"], drafts)
        return {
            "finding_ids": ids,
            "oppose": view.oppose.model_dump(),
            "node_results": ["n9:ok"],
            "counters": {"llm_calls": 1},
        }

    async def n10(state: ReviewState):
        current = deps.render_candidates.get(state["run_id"])
        envelope = GuardBatchEnvelope(
            items=[
                GuardInput(slot_no=x.slot_no, text=x.text, quoted=False, citations=x.citations)
                for x in current.candidate.slots
            ]
        )
        verdict, _ = await _invoke(deps, "n10", "LARGE", envelope, GuardVerdictDraft)
        deps.render_candidates.review(state["run_id"], verdict.violations)
        result = "pass" if not verdict.violations else "rewrite"
        if (
            verdict.violations
            and deps.render_candidates.get(state["run_id"]).rewrite_count >= REWRITE_LIMIT
        ):
            result = f"block:{ReasonCode.BUDGET_EXCEEDED.value}"
        return {
            "node_results": [f"n10:{result}"],
            "counters": {"llm_calls": 1, "rewrite": bool(verdict.violations)},
        }

    async def n11(state: ReviewState):
        if (
            deps.render_candidates.contains(state["run_id"])
            and deps.render_candidates.get(state["run_id"]).approved
        ):
            draft = deps.render_candidates.get(state["run_id"]).candidate
            evidence_ids = sorted({c.evidence_id for slot in draft.slots for c in slot.citations})
            evidence = await deps.evidence_store.get_many(evidence_ids)
            by_id = {item.evidence_id: item for item in evidence}
            validate_citations([c for slot in draft.slots for c in slot.citations], by_id)
            views = {
                item.evidence_id: RenderCitationView(
                    evidence_id=item.evidence_id,
                    span=item.raw_span,
                    source_url=item.source_url,
                    publisher=item.publisher,
                )
                for item in evidence
            }
            report = build_report_artifact(
                draft,
                banners=["COVERAGE_TRUNCATED"]
                if any("partial" in x for x in state["node_results"])
                else [],
                theory_notes=[],
                citation_views=views,
                created_at=deps.clock(),
            )
            report_id = await deps.review_store.put_report(
                state["run_id"], report.model_dump(mode="json")
            )
            return {"report_id": report_id, "node_results": ["n11:publish"]}
        feedback = (
            list(deps.render_candidates.get(state["run_id"]).guard_feedback)
            if deps.render_candidates.contains(state["run_id"])
            else []
        )
        evidence_ids = sorted(
            {
                eid
                for cid in state["claim_ids"]
                for eid in await deps.evidence_store.evidence_ids_for_claim(cid)
            }
        )
        evidence = await deps.evidence_store.get_many(evidence_ids)
        view = RenderView(
            slots=[SlotTextView(slot_no=1, text="검증 결과", quoted=False, citations=[])],
            banners=["COVERAGE_TRUNCATED"]
            if any("partial" in x for x in state["node_results"])
            else [],
            theory_notes=[],
            citations=[
                RenderCitationView(
                    evidence_id=x.evidence_id,
                    span=x.raw_span,
                    source_url=x.source_url,
                    publisher=x.publisher,
                )
                for x in evidence
            ],
            guard_feedback=feedback,
        )
        draft, _ = await _invoke(deps, "n11", "MID", view, RenderDraft)
        validate_citations(
            [c for slot in draft.slots for c in slot.citations],
            {x.evidence_id: x for x in evidence},
        )
        deps.render_candidates.put(state["run_id"], draft)
        return {"node_results": ["n11:generate"], "counters": {"llm_calls": 1}}

    async def n12(state: ReviewState):
        return {"node_results": ["n12:end"]}

    return {
        name: value
        for name, value in locals().items()
        if name
        in {"n0", "n1", "n2", "n3", "n3b", "n4", "n5", "n6", "n7", "n8", "n9", "n10", "n11", "n12"}
    }
