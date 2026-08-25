"""The fourteen thin S0 runtime vertices."""

from __future__ import annotations

from datetime import datetime
from functools import wraps
from time import perf_counter

from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
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
    ClaimView,
    ClassifiedEvidenceView,
    EvidenceExcerptView,
    EvidenceIntentView,
    EvidencePacket,
    GuardBatchEnvelope,
    GuardScanView,
    IntegrationView,
    RenderCitationView,
    RenderView,
    SlotTextView,
    VerifyPacket,
)
from app.diagnostics import debug_log, safe_exception_fields
from app.domain.evidence_need import EvidenceNeed, classify_evidence_need
from app.domain.evidence_requirement import EvidenceCategory, EvidenceRole
from app.domain.intake import (
    FreeTextInput,
    HybridIntake,
    IntakeMode,
    ResponseState,
    TargetSecurityInput,
)
from app.domain.routing import RoutingOutcome
from app.domain.semantic_source import SEMANTIC_PROJECTION_VERSION
from app.domain.slots import get_slot_definition
from app.domain.stock_scope import evaluate_stock_scope
from app.domain.text_safety import sanitize_user_text
from app.gateway.evidence_gateway import GatewayBudgetExceeded, collect_evidence
from app.orchestration.drafts import (
    EvidenceIntentDraft,
    EvidenceRequirementDraft,
    FindingDraft,
    GuardScanResult,
    GuardVerdictDraft,
    RenderDraft,
)
from app.orchestration.evidence_intent import validate_grounded_intent
from app.orchestration.evidence_packing import fits_budget, pack_evidence
from app.orchestration.evidence_planning import (
    RequirementStatus,
    plan_baseline_queries,
    plan_hybrid_claim,
)
from app.orchestration.hitl import StockChoiceRequest, StockChoiceResume, select_stock
from app.orchestration.intake_review_runtime import (
    HitlResumeEvent,
    InitialIntakeEvent,
    load_current_slot_projections,
    process_intake_review,
    reconstruct_ask_back_draft,
)
from app.orchestration.judgment_review import (
    build_judgment_review_drafts,
    build_missing_slot_views,
    build_review_slot_views,
    build_slot_projection_review_views,
    coalesce_slot_text_views,
)
from app.orchestration.limits import (
    EXTERNAL_CALL_LIMIT,
    HITL_REASK_LIMIT,
    REWRITE_LIMIT,
)
from app.orchestration.opposing_search import build_oppose_block
from app.orchestration.reporting import build_report_artifact
from app.orchestration.runtime import ReviewRequestContext, RuntimeDeps
from app.orchestration.state import ReviewState
from app.orchestration.validators.citations import validate_citations
from app.schemas.frozen import (
    PROVIDER_SOURCE_TYPE,
    CitationRef,
    Claim,
    ClaimEvaluationDraft,
    ClaimStanceDraft,
    CollectionResult,
    Evidence,
    GuardInput,
    NodeStatus,
    Query,
    ReasonCode,
    SourceTrace,
    StockCandidate,
)

_INCREASE_TERMS = ("증가", "상승", "오름", "늘", "개선", "increase", "increased", "rise", "rising", "up")
_DECREASE_TERMS = ("감소", "하락", "내림", "줄", "악화", "decrease", "decreased", "fall", "falling", "down")
_NEGATION_TERMS = ("않", "안 ", "못", "없", "not", "never", "didn't", "doesn't")
_FALLBACK_EVIDENCE_CATEGORIES = {
    EvidenceNeed.FINANCIAL_STATEMENT: EvidenceCategory.FINANCIAL_PERFORMANCE,
    EvidenceNeed.DISCLOSURE: EvidenceCategory.DISCLOSURE_EVENT,
    EvidenceNeed.NEWS: EvidenceCategory.NEWS_EVENT,
    EvidenceNeed.MARKET_PRICE: EvidenceCategory.PRICE_MOVEMENT,
    EvidenceNeed.INVESTOR_FLOW: EvidenceCategory.INVESTOR_FLOW,
}


def _minimum_evidence_intent(need: EvidenceNeed, text: str) -> EvidenceIntentDraft:
    """Keep one safe category when N5 returns an empty intent."""

    category = _FALLBACK_EVIDENCE_CATEGORIES.get(need)
    if need is EvidenceNeed.FINANCIAL_INDICATOR:
        category = (
            EvidenceCategory.PROFITABILITY
            if any(term in text for term in ("ROE", "ROA", "수익성"))
            else EvidenceCategory.FINANCIAL_STABILITY
            if any(term in text for term in ("부채비율", "안정성"))
            else EvidenceCategory.FINANCIAL_GROWTH
            if "성장성" in text
            else EvidenceCategory.OPERATING_EFFICIENCY
            if any(term in text for term in ("회전율", "활동성"))
            else None
        )
    return EvidenceIntentDraft(
        requirements=(
            [EvidenceRequirementDraft(category=category)]
            if category is not None
            else []
        )
    )


def _claim_direction(claim: Claim) -> str | None:
    text = claim.normalized_proposition.casefold()
    if any(term in text for term in _NEGATION_TERMS):
        return None
    increased = any(term.casefold() in text for term in _INCREASE_TERMS)
    decreased = any(term.casefold() in text for term in _DECREASE_TERMS)
    if increased == decreased:
        return None
    return "increase" if increased else "decrease"


def _structured_change_stance(claim: Claim, evidence: Evidence) -> str | None:
    """Return a stance only for directly comparable financial evidence."""
    value = evidence.normalized_value
    if not isinstance(value, dict) or value.get("kind") != "financial_statement":
        return None
    if value.get("comparison_available") is not True:
        return None
    account_name = value.get("account_name")
    evidence_direction = value.get("change_direction")
    if not isinstance(account_name, str) or account_name not in claim.normalized_proposition:
        return None
    if evidence_direction not in {"increase", "decrease", "unchanged"}:
        return None
    claim_direction = _claim_direction(claim)
    if claim_direction is None:
        return None
    if evidence_direction == "unchanged":
        return "oppose"
    return "support" if claim_direction == evidence_direction else "oppose"


def _apply_structured_stance_overrides(
    items: list,
    claim: Claim,
    evidence_by_id: dict[str, Evidence],
) -> list:
    return [
        item.model_copy(
            update={"stance": structured_stance, "stance_source": "rule"}
        )
        if (structured_stance := _structured_change_stance(
            claim, evidence_by_id[item.evidence_id]
        )) is not None
        else item
        for item in items
    ]


def _sanitize_intake(intake: HybridIntake) -> HybridIntake:
    target = (
        intake.target.model_copy(update={"name": sanitize_user_text(intake.target.name)})
        if intake.target is not None and intake.target.name is not None
        else intake.target
    )
    structured = tuple(
        item.model_copy(update={"value": sanitize_user_text(item.value)})
        if isinstance(item.value, str)
        and get_slot_definition(item.slot_id).value_shape == "text"
        else item
        for item in intake.structured
    )
    free_text = tuple(
        item.model_copy(update={"text": sanitize_user_text(item.text)})
        for item in intake.free_text
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
        if result.reason_code is None:
            suffix = "ok"
        elif result.reason_code is ReasonCode.INPUT_INSUFFICIENT:
            suffix = result.reason_code.value
        else:
            suffix = f"block:{result.reason_code.value}"
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
                candidates = deps.stock_resolver.resolve(target.name or "", limit=5)
                if not candidates:
                    return {
                        "node_results": [
                            f"n2:block:{ReasonCode.STOCK_UNRESOLVED.value}"
                        ]
                    }
                resume = None
                if len(candidates) > 1:
                    payload = interrupt(
                        StockChoiceRequest.from_candidates(target.name or "", candidates).model_dump()
                    )
                    resume = StockChoiceResume.model_validate(payload)
                selected = select_stock(candidates, resume)
                if selected is None:
                    return {
                        "node_results": [
                            f"n2:block:{ReasonCode.STOCK_UNRESOLVED.value}"
                        ]
                    }
                return {"stock": selected.model_dump(), "node_results": ["n2:ok"]}
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

    async def intake_review(state: ReviewState):
        claim_ids = list(state["claim_ids"])
        event = InitialIntakeEvent(
            run_id=state["run_id"],
            event_key=f"intake:{state['input_id']}",
            input_id=state["input_id"],
            run_started_at=datetime.fromisoformat(state["started_at"]),
            existing_claim_ids=tuple(claim_ids),
        )
        existing_records = await deps.review_store.get_ask_records(state["run_id"])
        existing_sources = await deps.review_store.get_resume_sources(state["run_id"])
        persisted_resume_keys = {item.resume_key for item in existing_sources}
        for record in existing_records:
            for claim_id in record.claim_ids:
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)
        pending_resume_keys = [
            f"resume:{item.ask_id}"
            for item in existing_records
            if f"resume:{item.ask_id}" not in persisted_resume_keys
        ]
        last_pending_resume_key = pending_resume_keys[-1] if pending_resume_keys else None
        replayed_result = None
        records_by_turn: dict[str, list] = {}
        for record in existing_records:
            turn_key = record.ask_key.rsplit(":ask:", 1)[0]
            records_by_turn.setdefault(turn_key, []).append(record)
        ordered_turns = sorted(
            records_by_turn.values(),
            key=lambda items: min(item.sequence for item in items),
        )
        hitl_limit_reached = len(ordered_turns) >= HITL_REASK_LIMIT
        for records in ordered_turns:
            draft = reconstruct_ask_back_draft(records)
            by_slot = {item.slot_id: item for item in records}
            questions = [
                {
                    "ask_id": by_slot[item.slot_id].ask_id,
                    "slot_id": item.slot_id,
                    "question": item.question,
                }
                for item in draft.questions
            ]
            payload = interrupt(
                {"schema_version": "intake_review_hitl/v1", "questions": questions}
            )
            if not isinstance(payload, dict):
                raise ValueError("HITL resume payload must be an object")
            answers = payload.get("answers")
            if answers is None and len(questions) == 1 and "answer" in payload:
                answers = [payload]
            if not isinstance(answers, list) or len(answers) != len(questions):
                raise ValueError("HITL resume must answer every emitted question")
            expected_ids = [item["ask_id"] for item in questions]
            actual_ids = [item.get("ask_id") for item in answers if isinstance(item, dict)]
            if actual_ids != expected_ids:
                raise ValueError("HITL answers must match emitted AskRecords in order")
            for answer in answers:
                resume_key = f"resume:{answer['ask_id']}"
                response_state = ResponseState(answer.get("response_state", "answered"))
                raw_answer = str(answer.get("answer", "")).strip() or None
                if response_state is ResponseState.ANSWERED and raw_answer is None:
                    raise ValueError("ANSWERED HITL response must be non-blank")
                if response_state is not ResponseState.ANSWERED and raw_answer is not None:
                    raise ValueError("non-answer HITL response must not carry answer text")
                event = HitlResumeEvent(
                    run_id=state["run_id"],
                    event_key=resume_key,
                    input_id=state["input_id"],
                    ask_id=answer["ask_id"],
                    raw_answer=raw_answer,
                    response_state=response_state,
                    run_started_at=datetime.fromisoformat(state["started_at"]),
                    existing_claim_ids=tuple(claim_ids),
                )
                if resume_key in persisted_resume_keys:
                    continue
                replay_result = await process_intake_review(
                    event,
                    review_store=deps.review_store,
                    model_gateway=deps.model_gateway,
                    persist_questions=(
                        resume_key == last_pending_resume_key
                        and not hitl_limit_reached
                    ),
                )
                replayed_result = replay_result
                persisted_resume_keys.add(resume_key)
                for claim_id in replay_result.persisted_claim_ids:
                    if claim_id not in claim_ids:
                        claim_ids.append(claim_id)
        while True:
            if replayed_result is not None:
                result = replayed_result
                replayed_result = None
            else:
                result = await process_intake_review(
                    event,
                    review_store=deps.review_store,
                    model_gateway=deps.model_gateway,
                )
            for claim_id in result.persisted_claim_ids:
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)

            if (
                result.routing_outcome is RoutingOutcome.NEEDS_HITL
                and hitl_limit_reached
            ):
                return Command(
                    update={
                        "claim_ids": claim_ids,
                        "node_results": ["intake_review:ready_for_evidence"],
                        "counters": {"hitl_reask": HITL_REASK_LIMIT},
                    },
                    goto="n5",
                )

            if result.routing_outcome is not RoutingOutcome.NEEDS_HITL:
                destination = {
                    RoutingOutcome.READY_FOR_EVIDENCE: "n5",
                    RoutingOutcome.CONTEXT_ONLY: "n5",
                    RoutingOutcome.BLOCKED: "n12",
                }[result.routing_outcome]
                ask_records = await deps.review_store.get_ask_records(state["run_id"])
                ask_turns = len(
                    {item.ask_key.rsplit(":ask:", 1)[0] for item in ask_records}
                )
                counters = {"hitl_reask": ask_turns} if ask_turns else {}
                return Command(
                    update={
                        "claim_ids": claim_ids,
                        "node_results": [
                            f"intake_review:{result.routing_outcome.value.lower()}"
                        ],
                        "counters": counters,
                    },
                    goto=destination,
                )

            records = await deps.review_store.get_ask_records(state["run_id"])
            prefix = f"{event.event_key}:ask:"
            by_slot = {
                item.slot_id: item for item in records if item.ask_key.startswith(prefix)
            }
            questions = []
            assert result.question_payload is not None
            for question in result.question_payload.questions:
                record = by_slot.get(question.slot_id)
                if record is None:
                    raise ValueError("question has no persisted AskRecord")
                questions.append(
                    {
                        "ask_id": record.ask_id,
                        "slot_id": question.slot_id,
                        "question": question.question,
                    }
                )
            payload = interrupt(
                {"schema_version": "intake_review_hitl/v1", "questions": questions}
            )
            if not isinstance(payload, dict):
                raise ValueError("HITL resume payload must be an object")
            answers = payload.get("answers")
            if answers is None and len(questions) == 1 and "answer" in payload:
                answers = [payload]
            if not isinstance(answers, list) or len(answers) != len(questions):
                raise ValueError("HITL resume must answer every emitted question")
            expected_ids = [item["ask_id"] for item in questions]
            actual_ids = [item.get("ask_id") for item in answers if isinstance(item, dict)]
            if actual_ids != expected_ids:
                raise ValueError("HITL answers must match emitted AskRecords in order")

            for index, answer in enumerate(answers):
                response_state = ResponseState(answer.get("response_state", "answered"))
                raw_answer = str(answer.get("answer", "")).strip() or None
                if response_state is ResponseState.ANSWERED and raw_answer is None:
                    raise ValueError("ANSWERED HITL response must be non-blank")
                if response_state is not ResponseState.ANSWERED and raw_answer is not None:
                    raise ValueError("non-answer HITL response must not carry answer text")
                event = HitlResumeEvent(
                    run_id=state["run_id"],
                    event_key=f"resume:{answer['ask_id']}",
                    input_id=state["input_id"],
                    ask_id=answer["ask_id"],
                    raw_answer=raw_answer,
                    response_state=response_state,
                    run_started_at=datetime.fromisoformat(state["started_at"]),
                    existing_claim_ids=tuple(claim_ids),
                )
                result = await process_intake_review(
                    event,
                    review_store=deps.review_store,
                    model_gateway=deps.model_gateway,
                    persist_questions=index == len(answers) - 1,
                )
                for claim_id in result.persisted_claim_ids:
                    if claim_id not in claim_ids:
                        claim_ids.append(claim_id)

    async def n5(state: ReviewState):
        try:
            claims = await canonical_claims(state["claim_ids"])
        except (KeyError, ValueError):
            return {
                "node_results": [
                    f"n5:block:{ReasonCode.CONTRACT_VIOLATION.value}"
                ]
            }
        stock = state.get("stock")
        if (
            not isinstance(stock, dict)
            or not isinstance(stock.get("code"), str)
            or not isinstance(stock.get("name"), str)
        ):
            return {
                "node_results": [
                    f"n5:block:{ReasonCode.CONTRACT_VIOLATION.value}"
                ]
            }
        as_of = datetime.fromisoformat(state["as_of"])
        baseline_queries = list(plan_baseline_queries(
            stock_code=stock["code"],
            stock_name=stock["name"],
            as_of=as_of,
            id_factory=deps.id_factory,
            clock=deps.clock,
        ))
        queries: list[Query] = list(baseline_queries)
        baseline_providers = {query.provider for query in baseline_queries}
        debug_log(
            "n5",
            "BASELINE",
            **{
                provider: (
                    "SOURCE_UNAVAILABLE"
                    if provider not in baseline_providers
                    else "READY"
                    if provider in deps.adapters
                    else "UNAVAILABLE"
                )
                for provider in ("dart", "kiwoom", "naver")
            },
        )
        relevant_claims = 0
        planned_claims = 0
        llm_calls = 0
        source_limited_claims = 0
        missing_user_fact_claims = 0
        for claim in claims:
            if not claim.verifiable:
                continue
            relevant_claims += 1
            need = classify_evidence_need(claim)
            view = EvidenceIntentView(
                claim_id=claim.claim_id,
                slot_id=claim.slot_id,
                normalized_proposition=claim.normalized_proposition,
                allowed_categories=list(EvidenceCategory),
            )
            intent_draft = None
            for _ in range(2):
                try:
                    candidate, _ = await _invoke(
                        deps, "n5", "SMALL", view, EvidenceIntentDraft
                    )
                    llm_calls += 1
                    intent_draft = validate_grounded_intent(
                        candidate, claim.normalized_proposition
                    )
                    break
                except (AssertionError, KeyError, RuntimeError, ValueError, ValidationError):
                    llm_calls += 1
            if intent_draft is None or not intent_draft.requirements:
                intent_draft = _minimum_evidence_intent(
                    need, claim.normalized_proposition
                )
            debug_log(
                "n5",
                "EVIDENCE_INTENT",
                claim_id=claim.claim_id,
                categories=[item.category.value for item in intent_draft.requirements],
            )
            plan = plan_hybrid_claim(
                claim,
                intent_draft,
                stock_code=stock["code"],
                stock_name=stock["name"],
                as_of=as_of,
                id_factory=deps.id_factory,
                clock=deps.clock,
            )
            queries.extend(plan.queries)
            planned_claims += plan.has_executable_primary
            source_limited_claims += any(
                item.status is RequirementStatus.SOURCE_UNAVAILABLE
                for item in plan.requirements
            )
            missing_user_fact_claims += any(
                item.status is RequirementStatus.MISSING_USER_FACT
                and item.role is EvidenceRole.PRIMARY
                for item in plan.requirements
            )
            for item in plan.requirements:
                debug_log(
                    "n5",
                    "REQUIREMENT_PLAN",
                    claim_id=claim.claim_id,
                    category=item.category.value,
                    provider=item.provider,
                    endpoint=item.endpoint,
                    role=item.role.value,
                    status=item.status.value,
                    missing_parameters=list(item.missing),
                )
            debug_log(
                "n5", "CLAIM_PLAN", claim_id=claim.claim_id,
                evidence_need=need.value,
                missing_parameters=sorted({value for item in plan.requirements for value in item.missing}),
                query_count=len(plan.queries),
                providers=sorted({item.provider for item in plan.queries}),
                endpoints=sorted({item.endpoint for item in plan.queries}),
            )
        ids = await deps.evidence_store.put_queries(state["run_id"], queries)
        node_status = (
            "ok"
            if relevant_claims == 0 or planned_claims == relevant_claims
            else "missing"
            if planned_claims == 0
            else "partial"
        )
        debug_log(
            "n5", "SUMMARY", baseline_queries=len(baseline_queries),
            primary_queries=sum(q.scope == "claim" and q.intent in {"verify", "counter"} for q in queries),
            corroborative_queries=sum(q.scope == "claim" and q.intent == "context" for q in queries),
            source_limited_claims=source_limited_claims,
            missing_user_fact_claims=missing_user_fact_claims,
        )
        patch = {"query_ids": ids, "node_results": [f"n5:{node_status}"]}
        if llm_calls:
            patch["counters"] = {"llm_calls": llm_calls}
        return patch

    async def n6(state: ReviewState):
        queries = await deps.evidence_store.get_queries(state["query_ids"])
        if not queries:
            debug_log(
                "n6",
                "COLLECTION_SKIP",
                reason="no_queries",
                query_count=0,
                external_calls=0,
            )
            return {
                "collections": {},
                "node_results": ["n6:missing"],
                "counters": {"external_calls": 0},
            }
        unavailable = sorted({query.provider for query in queries if query.provider not in deps.adapters})
        executable_queries = [query for query in queries if query.provider in deps.adapters]
        provider_counts: dict[str, int] = {}
        for query in queries:
            provider_counts[query.provider] = provider_counts.get(query.provider, 0) + 1
        debug_log(
            "n6",
            "COLLECTION_PLAN",
            query_count=len(queries),
            provider_counts=provider_counts,
            unavailable_providers=unavailable,
        )
        try:
            result = await collect_evidence(
                run_id=state["run_id"],
                as_of=datetime.fromisoformat(state["as_of"]),
                queries=executable_queries,
                adapters=deps.adapters,
                evidence_store=deps.evidence_store,
                provider_admission=deps.provider_admission,
                clock=deps.clock,
                id_factory=deps.id_factory,
                current_external_calls=state["counters"].get("external_calls", 0),
                external_call_limit=EXTERNAL_CALL_LIMIT,
            )
        except GatewayBudgetExceeded:
            return {"node_results": [f"n6:block:{ReasonCode.BUDGET_EXCEEDED.value}"]}
        collections = dict(result.collections)
        for provider in unavailable:
            collections[provider] = CollectionResult(
                source=PROVIDER_SOURCE_TYPE[provider],
                status=NodeStatus.MISSING,
                reason_code=ReasonCode.EVIDENCE_INSUFFICIENT,
                queries_run=0,
            ).model_dump(mode="json")
        statuses = {value["status"] for value in collections.values()}
        node_status = (
            "ok"
            if statuses <= {NodeStatus.OK.value}
            else "missing"
            if statuses == {NodeStatus.MISSING.value}
            else "partial"
        )
        debug_log(
            "n6",
            "COLLECTION_RESULT",
            external_calls=result.external_calls,
            collection_statuses=sorted(statuses),
            evidence_count=sum(
                int(value.get("items_adopted", 0))
                for value in collections.values()
            ),
        )
        return {
            "collections": collections,
            "node_results": [f"n6:{node_status}"],
            "counters": {"external_calls": result.external_calls},
        }

    async def n7(state: ReviewState):
        claims = await canonical_claims(state["claim_ids"])
        queries = await deps.evidence_store.get_queries(state["query_ids"])
        claim_ids = {claim.claim_id for claim in claims}
        query_ids_by_claim: dict[str, list[str]] = {}
        for query in queries:
            if query.scope != "claim":
                continue
            if query.claim_id not in claim_ids:
                raise RuntimeError(ReasonCode.CONTRACT_VIOLATION.value)
            query_ids_by_claim.setdefault(query.claim_id, []).append(query.query_id)
        llm_calls = 0
        degraded = False
        for claim in claims:
            if not claim.verifiable:
                continue
            evidence_ids = await deps.evidence_store.evidence_ids_for_claim(claim.claim_id)
            if not evidence_ids:
                continue
            query_ids = query_ids_by_claim.get(claim.claim_id)
            if not query_ids:
                raise RuntimeError(ReasonCode.CONTRACT_VIOLATION.value)
            query_ids_by_evidence: dict[str, set[str]] = {}
            for query_id in query_ids:
                for evidence_id in await deps.evidence_store.evidence_ids_for_queries(
                    [query_id]
                ):
                    query_ids_by_evidence.setdefault(evidence_id, set()).add(query_id)
            if set(query_ids_by_evidence) != set(evidence_ids):
                raise RuntimeError(ReasonCode.CONTRACT_VIOLATION.value)
            evidence = await deps.evidence_store.get_many(evidence_ids)
            claim_view = ClaimView(
                claim_id=claim.claim_id,
                slot_id=claim.slot_id,
                normalized_proposition=claim.normalized_proposition,
            )

            def _packet(
                items,
                claim_view=claim_view,
                query_ids_by_evidence=query_ids_by_evidence,
                queries=queries,
            ):
                return EvidencePacket(
                    claim=claim_view,
                    evidence=[
                        EvidenceExcerptView(
                            **item.model_dump(include=set(EvidenceExcerptView.model_fields)),
                            evidence_role=(
                                "PRIMARY"
                                if any(
                                    query.query_id in query_ids_by_evidence[item.evidence_id]
                                    and query.intent in {"verify", "counter"}
                                    for query in queries
                                )
                                else "CORROBORATIVE"
                            ),
                        )
                        for item in items
                    ],
                )

            evidence, dropped = pack_evidence(
                evidence,
                item_limit=NODE_BUDGETS["n7"].items,
                fits=lambda items: fits_budget("n7", _packet(items)),
            )
            if dropped:
                degraded = True
            # 🔴 조립기는 packet 과 **정확히 같은** evidence 집합을 요구한다
            #    (assemble_claim_evidence 의 coverage_mismatch / contract_violation).
            #    잘라낸 뒤에는 커버리지 기준도 잘린 쪽으로 좁혀야 한다.
            evidence_ids = [item.evidence_id for item in evidence]
            view = _packet(evidence)
            draft = None
            mapping = {
                evidence_id: (
                    next(iter(query_ids_by_evidence[evidence_id]))
                    if len(query_ids_by_evidence[evidence_id]) == 1
                    else None
                )
                for evidence_id in evidence_ids
            }
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
            draft = _apply_structured_stance_overrides(
                draft, claim, {item.evidence_id: item for item in evidence}
            )
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
        truncated = False
        queries = await deps.evidence_store.get_queries(state["query_ids"])
        primary_query_ids = [
            query.query_id
            for query in queries
            if query.scope == "claim" and query.intent in {"verify", "counter"}
        ]
        all_primary_evidence_ids = set(
            await deps.evidence_store.evidence_ids_for_queries(primary_query_ids)
        )
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
            # 🔴 n7 이 packet 을 잘랐으면 stance 가 없는 Evidence 가 남는다.
            #    n8 은 **n7 이 실제로 분류한 것**만 평가한다. 이 필터가 없으면
            #    아래 stance[...] 조회가 KeyError 로 run 을 끝낸다.
            evidence = [item for item in evidence if item.evidence_id in stance]
            if not evidence:
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
            claim_view = ClaimView(
                claim_id=claim.claim_id,
                slot_id=claim.slot_id,
                normalized_proposition=claim.normalized_proposition,
            )

            def _packet(items, claim_view=claim_view, stance=stance):
                return VerifyPacket(
                    claim=claim_view,
                    evidence=[
                        ClassifiedEvidenceView(
                            **item.model_dump(include=set(EvidenceExcerptView.model_fields)),
                            stance=stance[item.evidence_id],
                            evidence_role=(
                                "PRIMARY"
                                if item.evidence_id in all_primary_evidence_ids
                                else "CORROBORATIVE"
                            ),
                        )
                        for item in items
                    ],
                    numeric_checks=[],
                )

            evidence, dropped = pack_evidence(
                evidence,
                item_limit=NODE_BUDGETS["n8"].items,
                fits=lambda items: fits_budget("n8", _packet(items)),
            )
            if dropped:
                truncated = True
            evidence_ids = [item.evidence_id for item in evidence]
            view = _packet(evidence)
            assembled = None
            links_by_evidence = {item.evidence_id: item for item in links}
            rule_primary_oppose = {
                item.evidence_id
                for item in evidence
                if item.evidence_id in all_primary_evidence_ids
                and links_by_evidence[item.evidence_id].stance_source == "rule"
                and links_by_evidence[item.evidence_id].stance == "oppose"
            }
            rule_primary_support = {
                item.evidence_id
                for item in evidence
                if item.evidence_id in all_primary_evidence_ids
                and links_by_evidence[item.evidence_id].stance_source == "rule"
                and links_by_evidence[item.evidence_id].stance == "support"
            }
            if rule_primary_oppose and not rule_primary_support:
                deterministic_draft = ClaimEvaluationDraft(
                    citations=[
                        CitationRef(evidence_id=item.evidence_id, span=item.raw_span)
                        for item in evidence
                        if item.evidence_id in rule_primary_oppose
                    ],
                    support_evidence_ids=[
                        item.evidence_id
                        for item in evidence
                        if links_by_evidence[item.evidence_id].stance == "support"
                    ],
                    oppose_evidence_ids=[
                        item.evidence_id
                        for item in evidence
                        if links_by_evidence[item.evidence_id].stance == "oppose"
                    ],
                    neutral_evidence_ids=[
                        item.evidence_id
                        for item in evidence
                        if links_by_evidence[item.evidence_id].stance == "neutral"
                    ],
                    unknown_evidence_ids=[
                        item.evidence_id
                        for item in evidence
                        if links_by_evidence[item.evidence_id].stance == "unknown"
                    ],
                    verdict="contradicted",
                    missing_dimensions=[],
                    uncertainty_codes=[],
                )
                assembled = assemble_claim_evaluation(
                    deterministic_draft,
                    claim.claim_id,
                    evidence_ids,
                    [],
                    deps.id_factory(),
                    deps.clock(),
                    primary_evidence_ids=set(evidence_ids) & all_primary_evidence_ids,
                )
            else:
                for _ in range(2):
                    candidate, _ = await _invoke(deps, "n8", "LARGE", view, ClaimEvaluationDraft)
                    llm_calls += 1
                    try:
                        assembled = assemble_claim_evaluation(
                            candidate,
                            claim.claim_id,
                            evidence_ids,
                            [],
                            deps.id_factory(),
                            deps.clock(),
                            primary_evidence_ids=set(evidence_ids) & all_primary_evidence_ids,
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
        # packing 으로 잘린 것도 coverage 축소다. 모델이 스스로 신고하기를
        # 기다리지 않고 노드가 아는 사실을 그대로 partial 로 올린다.
        degraded = truncated or any(
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
        primary_evidence_ids = set(await deps.evidence_store.evidence_ids_for_queries([
            item.query_id
            for item in queries
            if item.scope == "claim" and item.intent in {"verify", "counter"}
        ]))
        deterministic_drafts = []
        evidence_backed = []
        for claim in claims:
            if not claim.verifiable:
                continue
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
            if set(evidence_ids) & primary_evidence_ids:
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
        counter_queries = [query for query in queries if query.intent == "counter"]
        provider_calls_by_query = {
            query.query_id: await deps.evidence_store.provider_calls_for_query(
                query.query_id
            )
            for query in counter_queries
        }
        evidence_ids_by_query = {
            query.query_id: await deps.evidence_store.evidence_ids_for_queries(
                [query.query_id]
            )
            for query in counter_queries
        }
        oppose_evidence_ids = {
            evidence_id
            for evaluation in evaluations
            for evidence_id in evaluation.oppose_evidence_ids
        }
        oppose = build_oppose_block(
            counter_queries=counter_queries,
            provider_calls_by_query=provider_calls_by_query,
            evidence_ids_by_query=evidence_ids_by_query,
            oppose_evidence_ids=oppose_evidence_ids,
        )
        projections = await load_current_slot_projections(
            state["run_id"],
            input_id=state.get("input_id"),
            review_store=deps.review_store,
        )
        oppose_evidence = await deps.evidence_store.get_many(
            sorted(oppose_evidence_ids)
        )
        oppose_citations = {
            item.evidence_id: CitationRef(
                evidence_id=item.evidence_id,
                span=item.raw_span,
            )
            for item in oppose_evidence
        }
        deterministic_drafts.extend(
            build_judgment_review_drafts(
                evaluations=evaluations,
                oppose=oppose,
                counter_claim_ids={
                    query.claim_id
                    for query in counter_queries
                    if query.claim_id is not None
                },
                projections=projections,
                citation_by_evidence_id=oppose_citations,
            )
        )
        view = IntegrationView(
            evaluations=evidence_backed,
            oppose=oppose,
            missing_slots=build_missing_slot_views(projections),
        )
        if not evidence_backed:
            ids = [deps.id_factory() for _ in deterministic_drafts]
            findings = assemble_findings(
                deterministic_drafts, evaluations, ids, deps.clock()
            )
            stored_ids = await deps.review_store.put_findings(
                state["run_id"], findings
            )
            # 🔴 근거 부족은 Report 를 없앨 이유가 아니라 **Report 에 실을 정보**다.
            #
            #    이전에는 Query 를 만들었는데 Evidence 가 0건이면
            #    block:evidence_insufficient -> n12 로 빠져 보고서가 아예 안 나왔다.
            #    그런데 Query 를 하나도 못 만든 경우(전부 UNKNOWN)에는 partial 로
            #    보고서가 나왔다. "검색할 게 없으면 보고서가 나오고, 검색했는데
            #    0건이면 안 나온다" 는 뒤집힌 결과였다.
            #
            #    뉴스 0건은 데모에서도 흔하다. "확인했으나 근거를 찾지 못했다" 는
            #    사용자에게 전달할 가치가 있는 결과이므로 partial 로 내보낸다.
            #    계약 위반과 안전 차단은 위쪽 분기에서 그대로 block 으로 남는다.
            return {
                "finding_ids": stored_ids,
                "oppose": view.oppose.model_dump(),
                "node_results": ["n9:partial"],
            }
        drafts = None
        llm_calls = 0
        for _ in range(2):
            llm_calls += 1
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
            "counters": {"llm_calls": llm_calls},
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
        violations = [item.to_canonical() for item in verdict.violations]
        deps.render_candidates.review(state["run_id"], violations)
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
        findings = await deps.review_store.get_findings(state["finding_ids"])
        evaluations = await deps.review_store.get_claim_evaluations(
            state["claim_evaluation_ids"]
        )
        projections = await load_current_slot_projections(
            state["run_id"],
            input_id=state.get("input_id"),
            review_store=deps.review_store,
        )
        review_slots = [
            *build_review_slot_views(findings, evaluations),
            *build_slot_projection_review_views(projections),
        ]
        review_slots = coalesce_slot_text_views(review_slots)
        # 🔴 n11 도 예산을 넘는다. citations 는 Evidence 전량이고 raw_span 이
        #    최대 500자라, 근거 8건이면 n11 상한(3,500자)을 넘긴다.
        #    slots 도 finding 수만큼 늘어나 item 상한(8)을 넘길 수 있다.
        fallback_slots = [
            SlotTextView(slot_no=1, text="검증 결과", quoted=False, citations=[])
        ]
        packed_slots = (review_slots or fallback_slots)[: NODE_BUDGETS["n11"].items]
        locally_truncated = len(packed_slots) < len(review_slots)

        def _render(items, slots=packed_slots, feedback=feedback, truncated=False):
            return RenderView(
                slots=slots,
                banners=["COVERAGE_TRUNCATED"]
                if truncated or any("partial" in x for x in state["node_results"])
                else [],
                theory_notes=[],
                citations=[
                    RenderCitationView(
                        evidence_id=x.evidence_id,
                        span=x.raw_span,
                        source_url=x.source_url,
                        publisher=x.publisher,
                    )
                    for x in items
                ],
                guard_feedback=feedback,
            )

        evidence, dropped = pack_evidence(
            evidence,
            item_limit=None,
            fits=lambda items: fits_budget("n11", _render(items)),
        )
        view = _render(evidence, truncated=locally_truncated or bool(dropped))
        draft, _ = await _invoke(deps, "n11", "MID", view, RenderDraft)
        validate_citations(
            [c for slot in draft.slots for c in slot.citations],
            {x.evidence_id: x for x in evidence},
        )
        deps.render_candidates.put(state["run_id"], draft)
        return {"node_results": ["n11:generate"], "counters": {"llm_calls": 1}}

    async def n12(state: ReviewState):
        return {"node_results": ["n12:end"]}

    def traced(name, node):
        @wraps(node)
        async def wrapper(*args, **kwargs):
            started = perf_counter()
            debug_log("graph", "START", node=name)
            try:
                result = await node(*args, **kwargs)
            except BaseException as error:
                event = "INTERRUPT" if type(error).__name__ == "GraphInterrupt" else "FAIL"
                fields = {
                    "node": name,
                    **safe_exception_fields(error),
                    "elapsed_ms": round((perf_counter() - started) * 1000, 1),
                }
                debug_log("graph", event, **fields)
                raise
            node_results = result.get("node_results", []) if isinstance(result, dict) else []
            semantic_result = node_results[-1] if node_results else None
            reason_code = None
            routing = None
            if isinstance(semantic_result, str) and ":block:" in semantic_result:
                reason_code = semantic_result.split(":block:", 1)[1]
                routing = "n12"
            debug_log(
                "graph", "END", node=name, status="ok",
                elapsed_ms=round((perf_counter() - started) * 1000, 1),
                node_results=node_results or None, node_result=semantic_result,
                reason_code=reason_code, route=routing,
            )
            return result

        return wrapper

    return {
        name: traced(name, value)
        for name, value in locals().items()
        if name
        in {"n0", "n1", "n2", "intake_review", "n5", "n6", "n7", "n8", "n9", "n10", "n11", "n12"}
    }
