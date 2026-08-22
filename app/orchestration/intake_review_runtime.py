"""Unified, graph-independent Intake/HITL orchestration boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.assemblers.semantic_extraction import (
    SemanticAssemblyError,
    SemanticAssemblyResult,
    SemanticAssemblyStatus,
    assemble_semantic_extraction,
)
from app.contexts.budget import validate_context_budget
from app.contexts.views import SemanticExtractionView, SemanticSegmentView
from app.domain.ask_history import (
    AskRecord,
    build_ask_record,
    project_hitl_context,
    reconstruct_ambiguity_issue,
)
from app.domain.hitl_policy import MAX_ASK_PER_TURN, AskTarget, select_ask_targets
from app.domain.intake import HybridIntake, ResponseState
from app.domain.missing import MissingInformation, MissingKind, analyze_missing
from app.domain.resume_source import (
    ResumeSemanticSource,
    build_resume_segment,
    build_resume_semantic_source,
)
from app.domain.routing import RoutingOutcome, decide_routing
from app.domain.semantic_source import (
    SEMANTIC_ANCHOR_SEPARATOR,
    SEMANTIC_PROJECTION_VERSION,
    SemanticTextSegment,
    build_semantic_segments,
)
from app.domain.slot_context import (
    ExtractionMethod,
    build_slot_observation,
)
from app.domain.slot_resolution import (
    CurrentSlotProjection,
    HydratedSlotObservation,
    ResolutionIssue,
    build_ambiguity_issue,
    resolve_current_slots,
    to_missing_observations,
)
from app.domain.slots import get_slot_definition
from app.models.protocols import ModelGateway
from app.orchestration.drafts import (
    AskBackDraft,
    AskBackQuestionDraft,
    SemanticExtractionDraft,
)
from app.orchestration.model_failure import (
    ModelFailure,
    ModelFailureCause,
    classify_model_failure,
)
from app.schemas.frozen import ULID, Claim, NonBlankStr, SourceTrace
from app.store.protocols import ReviewStore


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class InitialIntakeEvent(_RuntimeModel):
    kind: Literal["initial"] = "initial"
    run_id: NonBlankStr
    event_key: NonBlankStr
    input_id: ULID
    run_started_at: AwareDatetime
    existing_claim_ids: tuple[ULID, ...] = ()
    issues: tuple[ResolutionIssue, ...] = ()
    hard_blocked: bool = False


class HitlResumeEvent(_RuntimeModel):
    kind: Literal["resume"] = "resume"
    run_id: NonBlankStr
    event_key: NonBlankStr
    input_id: ULID
    ask_id: ULID
    raw_answer: NonBlankStr
    response_state: ResponseState = ResponseState.ANSWERED
    run_started_at: AwareDatetime
    existing_claim_ids: tuple[ULID, ...] = ()
    issues: tuple[ResolutionIssue, ...] = ()
    hard_blocked: bool = False


IntakeReviewEvent = Annotated[
    InitialIntakeEvent | HitlResumeEvent, Field(discriminator="kind")
]


class IntakeReviewResult(_RuntimeModel):
    routing_outcome: RoutingOutcome
    projections: tuple[CurrentSlotProjection, ...]
    missing: tuple[MissingInformation, ...]
    ask_targets: tuple[AskTarget, ...]
    persisted_observation_ids: tuple[ULID, ...] = ()
    persisted_claim_ids: tuple[ULID, ...] = ()
    issues: tuple[ResolutionIssue, ...] = ()
    question_payload: AskBackDraft | None = None

    @model_validator(mode="after")
    def enforce_question_routing(self):
        needs_hitl = self.routing_outcome is RoutingOutcome.NEEDS_HITL
        if needs_hitl != (self.question_payload is not None):
            raise ValueError("question payload is required only for NEEDS_HITL")
        return self


def _semantic_view(segments: tuple[SemanticTextSegment, ...]) -> SemanticExtractionView:
    return SemanticExtractionView(
        segments=tuple(
            SemanticSegmentView(
                segment_id=item.segment_id,
                locked_slot_id=item.locked_slot_id,
                text=item.text,
            )
            for item in segments
        )
    )


async def _invoke_and_assemble(
    *,
    run_id: str,
    segments: tuple[SemanticTextSegment, ...],
    structured_answers,
    existing_verifiable_claim_count: int,
    run_started_at: datetime,
    model_gateway: ModelGateway,
) -> SemanticAssemblyResult:
    if not segments:
        return assemble_semantic_extraction(
            None,
            run_id=run_id,
            projection_version=SEMANTIC_PROJECTION_VERSION,
            segments=(),
            structured_answers=structured_answers,
            existing_verifiable_claim_count=existing_verifiable_claim_count,
            run_started_at=run_started_at,
        )

    view = _semantic_view(segments)
    validate_context_budget("n3", view)
    completed_attempts = 0
    while True:
        completed_attempts += 1
        try:
            draft, _ = await model_gateway.invoke(
                "SMALL", "n3/v2", view, SemanticExtractionDraft
            )
        except Exception:
            decision = classify_model_failure(
                ModelFailure(cause=ModelFailureCause.MODEL_GATEWAY_ERROR),
                completed_model_attempts=completed_attempts,
            )
            if decision.retry_allowed:
                continue
            raise
        if not isinstance(draft, SemanticExtractionDraft):
            decision = classify_model_failure(
                ModelFailure(cause=ModelFailureCause.DRAFT_SCHEMA_INVALID),
                completed_model_attempts=completed_attempts,
            )
            if decision.retry_allowed:
                continue
            raise TypeError("model gateway returned a non-SemanticExtractionDraft")
        try:
            return assemble_semantic_extraction(
                draft,
                run_id=run_id,
                projection_version=SEMANTIC_PROJECTION_VERSION,
                segments=segments,
                structured_answers=structured_answers,
                existing_verifiable_claim_count=existing_verifiable_claim_count,
                run_started_at=run_started_at,
            )
        except SemanticAssemblyError as exc:
            decision = classify_model_failure(
                ModelFailure(
                    cause=ModelFailureCause.SEMANTIC_ASSEMBLY_ERROR,
                    semantic_error=exc,
                ),
                completed_model_attempts=completed_attempts,
            )
            if decision.retry_allowed:
                continue
            raise


def _append_resume_segments(
    initial: tuple[SemanticTextSegment, ...],
    sources: list[ResumeSemanticSource],
) -> tuple[SemanticTextSegment, ...]:
    result = list(initial)
    cursor = (
        result[-1].anchor_end + len(SEMANTIC_ANCHOR_SEPARATOR) if result else 0
    )
    for source in sources:
        segment = build_resume_segment(source, anchor_start=cursor)
        result.append(segment)
        cursor = segment.anchor_end + len(SEMANTIC_ANCHOR_SEPARATOR)
    return tuple(result)


async def _load_hydrated_history(
    run_id: str,
    input_body: dict,
    review_store: ReviewStore,
) -> tuple[HydratedSlotObservation, ...]:
    initial = build_semantic_segments(
        input_body["masked_intake"], input_body["semantic_projection_version"]
    )
    resume_sources = await review_store.get_resume_sources(run_id)
    segments = _append_resume_segments(initial, resume_sources)
    by_segment = {item.segment_id: item for item in segments}
    resume_by_segment = {item.segment_id: item for item in resume_sources}
    observations = await review_store.get_slot_observations(run_id)
    hydrated: list[HydratedSlotObservation] = []
    for observation in observations:
        text = None
        resolves: tuple[str, ...] = ()
        if observation.text_ref is not None:
            reference = observation.text_ref
            segment = by_segment.get(reference.segment_id)
            if segment is None or reference.local_end > len(segment.text):
                raise ValueError("slot observation references an unknown semantic source")
            if get_slot_definition(observation.slot_id).value_shape == "text":
                text = segment.text[reference.local_start : reference.local_end]
            resume_source = resume_by_segment.get(reference.segment_id)
            if resume_source is not None and resume_source.issue_id is not None:
                resolves = (resume_source.issue_id,)
        hydrated.append(
            HydratedSlotObservation(
                observation=observation,
                text=text,
                resolves_issue_ids=resolves,
            )
        )
    return tuple(hydrated)


def validate_ask_back_draft(
    draft: AskBackDraft,
    targets: tuple[AskTarget, ...],
) -> AskBackDraft:
    """Prevent wording generation from changing deterministic ask ownership."""

    if len(draft.questions) > MAX_ASK_PER_TURN:
        raise ValueError("question count exceeds MAX_ASK_PER_TURN")
    target_slots = {item.slot_id for item in targets}
    question_slots = [item.slot_id for item in draft.questions]
    if len(question_slots) != len(set(question_slots)):
        raise ValueError("duplicate question slot_id")
    if not set(question_slots).issubset(target_slots):
        raise ValueError("question slot_id was not selected as an AskTarget")
    if set(question_slots) != target_slots:
        raise ValueError("every AskTarget requires exactly one question")
    if tuple(question_slots) != tuple(item.slot_id for item in targets):
        raise ValueError("question order must match AskTarget priority order")
    return draft


def _template_questions(targets: tuple[AskTarget, ...]) -> AskBackDraft:
    questions = []
    for target in targets:
        label = get_slot_definition(target.slot_id).label
        if target.kind is MissingKind.CONFLICT:
            text = f"{label}에 서로 다른 답변이 있습니다. 현재 답변을 확인해 주세요."
        elif target.kind is MissingKind.AMBIGUOUS:
            text = f"{label}에 해당하는 의미가 불명확합니다. 의도를 확인해 주세요."
        else:
            text = f"{label}에 대해 알려 주세요."
        questions.append(AskBackQuestionDraft(slot_id=target.slot_id, question=text))
    return validate_ask_back_draft(AskBackDraft(questions=questions), targets)


def reconstruct_ask_back_draft(records: list[AskRecord]) -> AskBackDraft:
    """Rebuild one persisted ask turn for deterministic interrupt replay."""

    ordered = sorted(records, key=lambda item: item.sequence)
    targets = tuple(
        AskTarget(
            slot_id=item.slot_id,
            kind=item.kind,
            priority=0,
            reason=item.reason,
            required_for=(),
        )
        for item in ordered
    )
    return _template_questions(targets)


def _event_history(records: list[AskRecord], event_key: str) -> tuple[list[AskRecord], dict[int, AskRecord]]:
    prefix = f"{event_key}:ask:"
    current = {item.slot_id: item for item in records if item.ask_key.startswith(prefix)}
    prior = [item for item in records if not item.ask_key.startswith(prefix)]
    return prior, current


async def _persist_questions(
    *,
    run_id: str,
    event_key: str,
    targets: tuple[AskTarget, ...],
    projections: tuple[CurrentSlotProjection, ...],
    issues: tuple[ResolutionIssue, ...],
    claim_ids: tuple[str, ...],
    review_store: ReviewStore,
) -> AskBackDraft:
    payload = _template_questions(targets)
    records = await review_store.get_ask_records(run_id)
    _, current = _event_history(records, event_key)
    next_sequence = max((item.sequence for item in records), default=-1) + 1
    projection_by_slot = {item.slot_id: item for item in projections}
    issue_by_id = {item.issue_id: item for item in issues}
    additions: list[AskRecord] = []
    for index, target in enumerate(targets):
        existing = current.get(target.slot_id)
        issue_ids = projection_by_slot[target.slot_id].issue_ids
        if len(issue_ids) > 1:
            raise ValueError("one AskTarget cannot silently collapse multiple issues")
        issue_id = issue_ids[0] if issue_ids else None
        ambiguity = issue_by_id.get(issue_id) if target.kind is MissingKind.AMBIGUOUS else None
        if target.kind is MissingKind.AMBIGUOUS and ambiguity is None:
            raise ValueError("ambiguity AskTarget requires reconstructable issue lineage")
        record = build_ask_record(
            run_id,
            ask_key=f"{event_key}:ask:{target.slot_id}",
            target=target,
            issue_id=issue_id,
            issue_slot_ids=ambiguity.slot_ids if ambiguity is not None else (),
            issue_source_key=ambiguity.source_key if ambiguity is not None else None,
            sequence=existing.sequence if existing is not None else next_sequence + index,
            claim_ids=claim_ids,
        )
        additions.append(record)
    await review_store.put_ask_records(run_id, additions)
    return payload


async def _verifiable_claims(
    review_store: ReviewStore, claim_ids: tuple[str, ...]
) -> tuple[Claim, ...]:
    if not claim_ids:
        return ()
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("duplicate existing claim reference")
    claims = tuple(await review_store.get_claims(list(claim_ids)))
    if {item.claim_id for item in claims} != set(claim_ids):
        raise ValueError("existing claim reference coverage mismatch")
    return tuple(item for item in claims if item.verifiable)


def _empty_assembly(existing_claim_count: int) -> SemanticAssemblyResult:
    return SemanticAssemblyResult(
        status=SemanticAssemblyStatus.SUCCESS,
        capacity_plan={
            "materializable_indices": (),
            "eligible_count": 0,
            "existing_count": existing_claim_count,
            "capacity_exceeded": False,
        },
    )


async def process_intake_review(
    event: IntakeReviewEvent,
    *,
    review_store: ReviewStore,
    model_gateway: ModelGateway,
    persist_questions: bool = True,
) -> IntakeReviewResult:
    """Run initial or resume semantics through one deterministic control pipeline."""

    input_body = await review_store.get_input(event.input_id)
    existing_claims = await _verifiable_claims(review_store, event.existing_claim_ids)
    persisted_observation_ids: tuple[str, ...] = ()
    persisted_claim_ids: tuple[str, ...] = ()
    issues = list(event.issues)

    if event.hard_blocked:
        assembly = _empty_assembly(len(existing_claims))
    elif isinstance(event, InitialIntakeEvent):
        intake = HybridIntake.model_validate(
            {"schema_version": input_body["schema_version"], **input_body["masked_intake"]}
        )
        segments = build_semantic_segments(
            input_body["masked_intake"], input_body["semantic_projection_version"]
        )
        assembly = await _invoke_and_assemble(
            run_id=event.run_id,
            segments=segments,
            structured_answers=intake.structured,
            existing_verifiable_claim_count=len(existing_claims),
            run_started_at=event.run_started_at,
            model_gateway=model_gateway,
        )
    else:
        records = await review_store.get_ask_records(event.run_id)
        ask = next((item for item in records if item.ask_id == event.ask_id), None)
        if ask is None:
            raise ValueError("resume references an unknown AskRecord")
        if ask.kind is MissingKind.AMBIGUOUS:
            reconstructed = reconstruct_ambiguity_issue(ask)
            if all(item.issue_id != reconstructed.issue_id for item in issues):
                issues.append(reconstructed)
        source = build_resume_semantic_source(
            event.run_id,
            resume_key=event.event_key,
            slot_id=ask.slot_id,
            issue_id=ask.issue_id,
            raw_text=event.raw_answer,
        )
        if event.response_state is ResponseState.ANSWERED:
            segment = build_resume_segment(source, anchor_start=0)
            assembly = await _invoke_and_assemble(
                run_id=event.run_id,
                segments=(segment,),
                structured_answers=(),
                existing_verifiable_claim_count=len(existing_claims),
                run_started_at=event.run_started_at,
                model_gateway=model_gateway,
            )
            if assembly.status is SemanticAssemblyStatus.SUCCESS and (
                not assembly.observations
                or any(item.slot_id != ask.slot_id for item in assembly.observations)
            ):
                raise ValueError("resume semantics must produce only the asked Slot")
        else:
            observation = build_slot_observation(
                event.run_id,
                slot_id=ask.slot_id,
                response_state=event.response_state,
                origin=SourceTrace.USER_CONFIRMED,
                extraction_method=ExtractionMethod.DIRECT,
                value=None,
                text_ref=None,
            )
            assembly = _empty_assembly(len(existing_claims)).model_copy(
                update={"observations": (observation,)}
            )
        if assembly.status is SemanticAssemblyStatus.SUCCESS:
            await review_store.put_resume_sources(event.run_id, [source])

    if assembly.status is SemanticAssemblyStatus.SUCCESS:
        observation_ids, claim_ids = await review_store.put_semantic_batch(
            event.run_id, list(assembly.observations), list(assembly.claims)
        )
        persisted_observation_ids = tuple(observation_ids)
        persisted_claim_ids = tuple(claim_ids)
    elif assembly.status is SemanticAssemblyStatus.AMBIGUOUS:
        issues.extend(
            build_ambiguity_issue(
                slot_ids=item.slot_ids,
                source_key=(
                    f"{item.segment_id}:{item.span_offset[0]}:{item.span_offset[1]}"
                ),
            )
            for item in assembly.ambiguities
        )

    hydrated = await _load_hydrated_history(event.run_id, input_body, review_store)
    projections = resolve_current_slots(hydrated, issues=issues)
    missing = analyze_missing(to_missing_observations(projections))
    all_records = await review_store.get_ask_records(event.run_id)
    prior_records, _ = _event_history(all_records, event.event_key)
    ask_targets = select_ask_targets(missing, project_hitl_context(prior_records))
    verifiable_count = len(
        {item.claim_id for item in existing_claims}
        | {item.claim_id for item in assembly.claims if item.verifiable}
    )
    outcome = decide_routing(
        projections,
        missing,
        ask_targets,
        verifiable_claim_count=verifiable_count,
        hard_blocked=(
            event.hard_blocked
            or assembly.status is SemanticAssemblyStatus.CAPACITY_EXCEEDED
        ),
    )
    question_payload = None
    if outcome is RoutingOutcome.NEEDS_HITL:
        if persist_questions:
            question_payload = await _persist_questions(
                run_id=event.run_id,
                event_key=event.event_key,
                targets=ask_targets,
                projections=projections,
                issues=tuple(issues),
                claim_ids=tuple(
                    sorted(
                        set(event.existing_claim_ids)
                        | {item.claim_id for item in assembly.claims}
                    )
                ),
                review_store=review_store,
            )
        else:
            question_payload = _template_questions(ask_targets)
    return IntakeReviewResult(
        routing_outcome=outcome,
        projections=projections,
        missing=missing,
        ask_targets=ask_targets,
        persisted_observation_ids=persisted_observation_ids,
        persisted_claim_ids=persisted_claim_ids,
        issues=tuple(sorted(set(issues), key=lambda item: item.issue_id)),
        question_payload=question_payload,
    )
