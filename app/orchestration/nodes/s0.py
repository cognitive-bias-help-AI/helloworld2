"""The fourteen thin S0 runtime vertices."""

from __future__ import annotations

import re
from hashlib import sha256

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

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
from app.domain.intake import FreeTextInput, HybridIntake, IntakeMode, TargetSecurityInput
from app.domain.semantic_source import SEMANTIC_PROJECTION_VERSION
from app.domain.slots import get_slot_definition
from app.domain.stock_scope import evaluate_stock_scope
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
    StockCandidate,
)


def _mask(value: str) -> str:
    value = " ".join(value.split())
    value = re.sub(r"[\w.+-]+@[\w.-]+", "[EMAIL]", value)
    return re.sub(r"\b01[016789]-?\d{3,4}-?\d{4}\b", "[PHONE]", value)


def _sanitize_intake(intake: HybridIntake) -> HybridIntake:
    target = (
        intake.target.model_copy(update={"name": _mask(intake.target.name)})
        if intake.target is not None and intake.target.name is not None
        else intake.target
    )
    structured = tuple(
        item.model_copy(update={"value": _mask(item.value)})
        if isinstance(item.value, str)
        and get_slot_definition(item.slot_id).value_shape == "text"
        else item
        for item in intake.structured
    )
    free_text = tuple(
        item.model_copy(update={"text": _mask(item.text)}) for item in intake.free_text
    )
    return intake.model_copy(
        update={"target": target, "structured": structured, "free_text": free_text}
    )


def _security_projection(intake: HybridIntake) -> str:
    segments = [intake.target.name] if intake.target is not None and intake.target.name else []
    segments.extend(
        item.value
        for item in sorted(intake.structured, key=lambda item: item.slot_id)
        if isinstance(item.value, str)
        and get_slot_definition(item.slot_id).value_shape == "text"
    )
    segments.extend(item.text for item in intake.free_text)
    return "\n".join(segment for segment in segments if segment)


def _budget(node: str, view) -> None:
    limit = NODE_BUDGETS[node]
    if ctx_chars(view) > limit.chars or (limit.items is not None and ctx_items(view) > limit.items):
        raise RuntimeError(ReasonCode.BUDGET_EXCEEDED.value)


async def _invoke(deps: RuntimeDeps, node: str, slot: str, view, schema):
    _budget(node, view)
    return await deps.model_gateway.invoke(slot, f"{node}/v1", view, schema)


def make_nodes(deps: RuntimeDeps):
    async def canonical_claims(claim_ids: list[str]):
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("duplicate claim reference")
        claims = await deps.review_store.get_claims(claim_ids)
        by_id = {item.claim_id: item for item in claims}
        if len(by_id) != len(claims) or set(by_id) != set(claim_ids):
            raise ValueError("claim reference coverage mismatch")
        return [by_id[claim_id] for claim_id in claim_ids]

    async def n0(state: ReviewState, runtime: Runtime[ReviewRequestContext]):
        if runtime.context is None:
            raise ValueError("ReviewRequestContext.raw_text is required")
        if runtime.context.intake is not None:
            intake = runtime.context.intake
        else:
            raw_text = runtime.context.raw_text
            if raw_text is None or not raw_text.strip():
                raise ValueError("ReviewRequestContext.raw_text is required")
            intake = HybridIntake(
                schema_version="hybrid_intake/v1",
                mode=IntakeMode.CHAT_FIRST,
                free_text=(
                    FreeTextInput(text=raw_text, source=SourceTrace.CHAT_EXPLICIT),
                ),
            )
        intake = _sanitize_intake(intake)
        body = {
            "schema_version": intake.schema_version,
            "semantic_projection_version": SEMANTIC_PROJECTION_VERSION,
            "masked_intake": intake.model_dump(mode="json", exclude={"schema_version"}),
            "masked_input": "\n".join(item.text for item in intake.free_text),
            "masked_security_input": _security_projection(intake),
        }
        input_id = await deps.review_store.put_input(state["run_id"], body)
        return {"input_id": input_id, "node_results": ["n0:ok"]}

    async def n1(state: ReviewState):
        body = await deps.review_store.get_input(state["input_id"])
        if not body["masked_security_input"]:
            return {"node_results": ["n1:ok"]}
        result, _ = await _invoke(
            deps,
            "n1",
            "SMALL",
            GuardScanView(masked_input=body["masked_security_input"]),
            GuardScanResult,
        )
        suffix = "ok" if result.reason_code is None else f"block:{result.reason_code.value}"
        return {"node_results": [f"n1:{suffix}"], "counters": {"llm_calls": 1}}

    async def n2(state: ReviewState):
        body = await deps.review_store.get_input(state["input_id"])
        target_body = body.get("masked_intake", {}).get("target")
        if target_body is not None:
            try:
                target = TargetSecurityInput.model_validate(target_body)
            except ValidationError:
                return {
                    "node_results": [
                        f"n2:block:{ReasonCode.STOCK_UNRESOLVED.value}"
                    ]
                }
            if target.selected_code is None:
                return {
                    "node_results": [
                        f"n2:block:{ReasonCode.STOCK_UNRESOLVED.value}"
                    ]
                }
            exact = deps.stock_resolver.resolve_exact(target.selected_code)
            if len(exact) > 1 or (
                len(exact) == 1 and exact[0].code != target.selected_code
            ):
                return {
                    "node_results": [
                        f"n2:block:{ReasonCode.CONTRACT_VIOLATION.value}"
                    ]
                }
            if not exact:
                return {
                    "node_results": [
                        f"n2:block:{ReasonCode.STOCK_UNRESOLVED.value}"
                    ]
                }
            instrument = exact[0]
            if not evaluate_stock_scope(instrument).supported:
                return {
                    "node_results": [f"n2:block:{ReasonCode.OUT_OF_SCOPE.value}"]
                }
            selected = StockCandidate(
                code=instrument.code,
                name=instrument.name,
                market=instrument.market,
                match_kind="exact_code",
                score=1.0,
                is_delisted=instrument.is_delisted,
                is_managed=instrument.is_managed,
            )
            return {"stock": selected.model_dump(), "node_results": ["n2:ok"]}

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
        try:
            claims = await canonical_claims(state["claim_ids"])
        except (KeyError, ValueError):
            return {
                "node_results": [
                    f"n5:block:{ReasonCode.CONTRACT_VIOLATION.value}"
                ]
            }
        queries = [
            Query(
                query_id=deps.id_factory(),
                scope="claim",
                claim_id=claim.claim_id,
                intent="verify",
                provider="dart",
                endpoint="disclosure",
                params={"stock_code": state["stock"]["code"]},
                created_at=deps.clock(),
            )
            for claim in claims
            if claim.verifiable
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
        claims = await canonical_claims(state["claim_ids"])
        queries = await deps.evidence_store.get_queries(state["query_ids"])
        query_by_claim = {}
        for query in queries:
            if query.scope != "claim":
                continue
            if query.claim_id in query_by_claim:
                raise RuntimeError(ReasonCode.CONTRACT_VIOLATION.value)
            query_by_claim[query.claim_id] = query.query_id
        llm_calls = 0
        degraded = False
        for claim in claims:
            if not claim.verifiable:
                continue
            evidence_ids = await deps.evidence_store.evidence_ids_for_claim(claim.claim_id)
            if not evidence_ids:
                continue
            query_id = query_by_claim.get(claim.claim_id)
            if query_id is None:
                raise RuntimeError(ReasonCode.CONTRACT_VIOLATION.value)
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
            mapping = {evidence_id: query_id for evidence_id in evidence_ids}
            for _ in range(2):
                candidate, _ = await _invoke(deps, "n7", "SMALL", view, ClaimStanceDraft)
                llm_calls += 1
                try:
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
                degraded = True
            await deps.review_store.put_claim_evidence(state["run_id"], draft)
        patch = {
            "node_results": ["n7:partial" if degraded else "n7:ok"],
        }
        if llm_calls:
            patch["counters"] = {"llm_calls": llm_calls}
        return patch

    async def n8(state: ReviewState):
        evaluations = []
        llm_calls = 0
        for claim in await canonical_claims(state["claim_ids"]):
            if not claim.verifiable:
                continue
            evidence_ids = await deps.evidence_store.evidence_ids_for_claim(claim.claim_id)
            if not evidence_ids:
                evaluations.append(
                    assemble_unverifiable_evaluation_fallback(
                        claim_id=claim.claim_id,
                        packet_evidence_ids=[],
                        numeric_checks=[],
                        claim_evaluation_id=deps.id_factory(),
                        created_at=deps.clock(),
                    )
                )
                continue
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
                llm_calls += 1
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
        patch = {
            "claim_evaluation_ids": ids,
            "node_results": ["n8:partial" if degraded else "n8:ok"],
        }
        if llm_calls:
            patch["counters"] = {"llm_calls": llm_calls}
        return patch

    async def n9(state: ReviewState):
        evaluations = await deps.review_store.get_claim_evaluations(state["claim_evaluation_ids"])
        claims = await canonical_claims(state["claim_ids"])
        evaluations_by_claim = {item.claim_id: item for item in evaluations}
        if len(evaluations_by_claim) != len(evaluations):
            return {
                "node_results": [
                    f"n9:block:{ReasonCode.CONTRACT_VIOLATION.value}"
                ]
            }
        queries = await deps.evidence_store.get_queries(state["query_ids"])
        queried_claim_ids = {
            item.claim_id for item in queries if item.scope == "claim"
        }
        deterministic_drafts = []
        evidence_backed = []
        for claim in claims:
            if not claim.verifiable:
                continue
            if claim.claim_id not in queried_claim_ids:
                return {
                    "node_results": [
                        f"n9:block:{ReasonCode.CONTRACT_VIOLATION.value}"
                    ]
                }
            evaluation = evaluations_by_claim.get(claim.claim_id)
            if evaluation is None:
                return {
                    "node_results": [
                        f"n9:block:{ReasonCode.CONTRACT_VIOLATION.value}"
                    ]
                }
            evidence_ids = await deps.evidence_store.evidence_ids_for_claim(
                claim.claim_id
            )
            if evidence_ids:
                evidence_backed.append(evaluation)
            else:
                deterministic_drafts.append(
                    FindingDraft(
                        slot_id=claim.slot_id,
                        kind="unverified",
                        citations=[],
                        claim_evaluation_id=evaluation.claim_evaluation_id,
                    )
                )
        view = IntegrationView(
            evaluations=evidence_backed,
            oppose=OpposeBlock(status="verified", count=0, queries=["반대 근거 검색"]),
            missing_slots=[],
        )
        if not evidence_backed:
            ids = [deps.id_factory() for _ in deterministic_drafts]
            findings = assemble_findings(
                deterministic_drafts, evaluations, ids, deps.clock()
            )
            stored_ids = await deps.review_store.put_findings(
                state["run_id"], findings
            )
            return {
                "finding_ids": stored_ids,
                "oppose": view.oppose.model_dump(),
                "node_results": [
                    f"n9:block:{ReasonCode.EVIDENCE_INSUFFICIENT.value}"
                ],
            }
        drafts = None
        for _ in range(2):
            candidate, _ = await _invoke(deps, "n9", "LARGE", view, FindingDraft)
            values = [*deterministic_drafts, candidate]
            try:
                drafts = assemble_findings(
                    values,
                    evaluations,
                    [deps.id_factory() for _ in values],
                    deps.clock(),
                )
                break
            except AssemblyError as exc:
                if not exc.retryable:
                    raise
        if drafts is None:
            values = deterministic_drafts
            drafts = (
                assemble_findings(
                    values,
                    evaluations,
                    [deps.id_factory() for _ in values],
                    deps.clock(),
                )
                if values
                else omit_invalid_findings_fallback()
            )
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
