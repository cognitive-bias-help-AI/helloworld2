from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.intake import HybridIntake, IntakeMode, ResponseState, StructuredAnswer
from app.domain.routing import RoutingOutcome
from app.domain.semantic import SemanticKind
from app.orchestration.drafts import (
    AskBackDraft,
    AskBackQuestionDraft,
    SemanticExtractionDraft,
    SemanticUnitDraft,
)
from app.orchestration.intake_review_runtime import (
    HitlResumeEvent,
    InitialIntakeEvent,
    load_current_slot_projections,
    process_intake_review,
    validate_ask_back_draft,
)
from app.schemas.frozen import SourceTrace, Usage
from app.store.memory_review_store import MemoryReviewStore

NOW = datetime(2026, 8, 20, tzinfo=UTC)


class Gateway:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.calls = []

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        self.calls.append((slot, prompt_version, input_view, output_schema))
        draft = self.drafts.pop(0)
        if isinstance(draft, Exception):
            raise draft
        return draft, Usage(
            model_slot=slot,
            prompt_tokens=0,
            output_tokens=0,
            ctx_chars=len(input_view.model_dump_json()),
        )


def answer(slot_id, value, source=SourceTrace.SURVEY, state=ResponseState.ANSWERED):
    return StructuredAnswer(
        slot_id=slot_id,
        value=value,
        source=source,
        response_state=state,
    )


async def stored_input(store, *, structured=(), free_text=()):
    intake = HybridIntake(
        schema_version="hybrid_intake/v1",
        mode=IntakeMode.HYBRID,
        structured=tuple(structured),
        free_text=tuple(free_text),
    )
    body = {
        "schema_version": "hybrid_intake/v1",
        "semantic_projection_version": "semantic_projection/v1",
        "masked_intake": intake.model_dump(mode="json", exclude={"schema_version"}),
        "masked_input": "\n".join(item.text for item in intake.free_text),
        "masked_security_input": "safe",
    }
    return await store.put_input("run-1", body)


def complete_structured():
    return (
        answer(1, "CONSIDER_ENTRY"),
        answer(2, "NOT_HOLDING"),
        answer(3, "LONG"),
        answer(5, "장기 성장"),
        answer(8, "전제가 바뀌면 재검토"),
    )


@pytest.mark.asyncio
async def test_current_slot_projection_can_be_reloaded_from_persisted_runtime_history():
    store = MemoryReviewStore()
    input_id = await stored_input(store, structured=complete_structured())
    draft = SemanticExtractionDraft(
        units=[
            SemanticUnitDraft(
                segment_id="structured:5",
                slot_id=5,
                text_span="장기 성장",
                span_offset=(0, len("장기 성장")),
                normalized_proposition=None,
                proposed_value=None,
                semantic_kind=SemanticKind.USER_PREFERENCE,
            ),
            SemanticUnitDraft(
                segment_id="structured:8",
                slot_id=8,
                text_span="전제가 바뀌면 재검토",
                span_offset=(0, len("전제가 바뀌면 재검토")),
                normalized_proposition=None,
                proposed_value=None,
                semantic_kind=SemanticKind.DECISION_RULE,
            ),
        ]
    )
    await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1",
            event_key="initial-projection",
            input_id=input_id,
            run_started_at=NOW,
        ),
        review_store=store,
        model_gateway=Gateway([draft]),
    )

    projections = await load_current_slot_projections(
        "run-1", input_id=input_id, review_store=store
    )

    assert projections[6].slot_id == 7 and projections[6].status.value == "ABSENT"
    assert projections[7].slot_id == 8 and projections[7].status.value == "RESOLVED"


@pytest.mark.asyncio
async def test_initial_semantics를_persist하고_READY_FOR_EVIDENCE를_계산한다():
    from app.domain.intake import FreeTextInput

    store = MemoryReviewStore()
    text = "HBM demand grows"
    input_id = await stored_input(
        store,
        structured=complete_structured(),
        free_text=(FreeTextInput(text=text, source=SourceTrace.CHAT_EXPLICIT),),
    )
    draft = SemanticExtractionDraft(
        units=[
            SemanticUnitDraft(
                segment_id="free_text:0",
                slot_id=4,
                text_span=text,
                span_offset=(0, len(text)),
                normalized_proposition=text,
                proposed_value=None,
                semantic_kind=SemanticKind.EXTERNAL_ASSERTION,
            )
        ]
    )

    result = await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1",
            event_key="initial-1",
            input_id=input_id,
            run_started_at=NOW,
        ),
        review_store=store,
        model_gateway=Gateway([draft]),
    )

    assert result.routing_outcome is RoutingOutcome.READY_FOR_EVIDENCE
    assert len(result.projections) == 8
    assert result.persisted_observation_ids
    assert len(result.persisted_claim_ids) == 1
    assert result.question_payload is None


@pytest.mark.asyncio
async def test_missing은_AskTarget과_deterministic_questions_ask_history를_만든다():
    store = MemoryReviewStore()
    input_id = await stored_input(store, structured=(answer(1, "CONSIDER_ENTRY"),))

    result = await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1",
            event_key="initial-1",
            input_id=input_id,
            run_started_at=NOW,
        ),
        review_store=store,
        model_gateway=Gateway([SemanticExtractionDraft(units=[])]),
    )

    assert result.routing_outcome is RoutingOutcome.NEEDS_HITL
    assert 0 < len(result.ask_targets) <= 2
    assert tuple(q.slot_id for q in result.question_payload.questions) == tuple(
        target.slot_id for target in result.ask_targets
    )
    records = await store.get_ask_records("run-1")
    assert [record.slot_id for record in records] == [item.slot_id for item in result.ask_targets]


@pytest.mark.asyncio
async def test_malformed_or_failed_semantics는_canonical_mutation을_남기지_않는다():
    from app.domain.intake import FreeTextInput

    store = MemoryReviewStore()
    input_id = await stored_input(
        store,
        structured=(answer(1, "CONSIDER_ENTRY"),),
        free_text=(FreeTextInput(text="x", source=SourceTrace.CHAT_EXPLICIT),),
    )
    bad = SemanticExtractionDraft(
        units=[
            SemanticUnitDraft(
                segment_id="unknown",
                slot_id=2,
                text_span="x",
                span_offset=(0, 1),
                normalized_proposition=None,
                proposed_value="NOT_HOLDING",
                semantic_kind=SemanticKind.USER_STATE,
            )
        ]
    )

    with pytest.raises(ValueError):
        await process_intake_review(
            InitialIntakeEvent(
                run_id="run-1",
                event_key="initial-1",
                input_id=input_id,
                run_started_at=NOW,
            ),
            review_store=store,
            model_gateway=Gateway([bad, bad]),
        )

    assert await store.get_slot_observations("run-1") == []
    assert await store.get_ask_records("run-1") == []


@pytest.mark.asyncio
async def test_model_gateway_failure는_기존_policy대로_한번만_retry한다():
    from app.domain.intake import FreeTextInput

    store = MemoryReviewStore()
    input_id = await stored_input(
        store,
        free_text=(FreeTextInput(text="개인 관심", source=SourceTrace.CHAT_EXPLICIT),),
    )
    text = "개인 관심"
    draft = SemanticExtractionDraft(
        units=[
            SemanticUnitDraft(
                segment_id="free_text:0",
                slot_id=4,
                text_span=text,
                span_offset=(0, len(text)),
                normalized_proposition=None,
                proposed_value=None,
                semantic_kind=SemanticKind.USER_PREFERENCE,
            )
        ]
    )
    gateway = Gateway([RuntimeError("temporary"), draft])

    await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1", event_key="initial-1", input_id=input_id, run_started_at=NOW
        ),
        review_store=store,
        model_gateway=gateway,
    )

    assert len(gateway.calls) == 2


@pytest.mark.asyncio
async def test_initial_exact_replay는_semantic과_ask_identity를_중복하지_않는다():
    store = MemoryReviewStore()
    input_id = await stored_input(store, structured=(answer(1, "CONSIDER_ENTRY"),))
    event = InitialIntakeEvent(
        run_id="run-1",
        event_key="initial-1",
        input_id=input_id,
        run_started_at=NOW,
    )
    gateway = Gateway([SemanticExtractionDraft(units=[]), SemanticExtractionDraft(units=[])])

    first = await process_intake_review(event, review_store=store, model_gateway=gateway)
    replay = await process_intake_review(event, review_store=store, model_gateway=gateway)

    assert first.persisted_observation_ids == replay.persisted_observation_ids
    assert first.ask_targets == replay.ask_targets
    assert len(await store.get_ask_records("run-1")) == len(first.ask_targets)


@pytest.mark.asyncio
async def test_hard_block은_model과_canonical_mutation보다_먼저_BLOCKED다():
    from app.domain.intake import FreeTextInput

    store = MemoryReviewStore()
    input_id = await stored_input(
        store,
        free_text=(FreeTextInput(text="HBM grows", source=SourceTrace.CHAT_EXPLICIT),),
    )
    gateway = Gateway([])

    result = await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1",
            event_key="initial-1",
            input_id=input_id,
            run_started_at=NOW,
            hard_blocked=True,
        ),
        review_store=store,
        model_gateway=gateway,
    )

    assert result.routing_outcome is RoutingOutcome.BLOCKED
    assert gateway.calls == []
    assert await store.get_slot_observations("run-1") == []
    assert await store.get_ask_records("run-1") == []


@pytest.mark.asyncio
async def test_claim없는_resolved_intake는_CONTEXT_ONLY다():
    store = MemoryReviewStore()
    input_id = await stored_input(
        store,
        structured=(*complete_structured(), answer(4, "개인 관심")),
    )

    result = await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1", event_key="initial-1", input_id=input_id, run_started_at=NOW
        ),
        review_store=store,
        model_gateway=Gateway([SemanticExtractionDraft(units=[])]),
    )

    assert result.routing_outcome is RoutingOutcome.CONTEXT_ONLY
    assert result.persisted_claim_ids == ()


@pytest.mark.asyncio
async def test_semantic_ambiguity는_issue와_AMBIGUOUS_projection을_보존한다():
    from app.domain.intake import FreeTextInput
    from app.domain.slot_resolution import CurrentSlotStatus

    store = MemoryReviewStore()
    text = "growth"
    input_id = await stored_input(
        store,
        free_text=(FreeTextInput(text=text, source=SourceTrace.CHAT_EXPLICIT),),
    )
    unit = dict(
        segment_id="free_text:0",
        text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=None,
        proposed_value=None,
        semantic_kind=SemanticKind.USER_PREFERENCE,
    )
    draft = SemanticExtractionDraft(
        units=[SemanticUnitDraft(slot_id=4, **unit), SemanticUnitDraft(slot_id=5, **unit)]
    )

    result = await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1", event_key="initial-1", input_id=input_id, run_started_at=NOW
        ),
        review_store=store,
        model_gateway=Gateway([draft]),
    )

    assert len(result.issues) == 1
    assert result.projections[3].status is CurrentSlotStatus.AMBIGUOUS
    assert result.projections[4].status is CurrentSlotStatus.AMBIGUOUS
    assert await store.get_slot_observations("run-1") == []


@pytest.mark.asyncio
async def test_ambiguity_AskRecord_v2_lineage만으로_resume_issue를_재구성한다():
    from app.domain.intake import FreeTextInput
    from app.domain.resume_source import build_resume_semantic_source

    store = MemoryReviewStore()
    text = "growth"
    input_id = await stored_input(
        store,
        structured=(
            answer(1, "CONSIDER_ENTRY"),
            answer(2, "NOT_HOLDING"),
            answer(3, "LONG"),
        ),
        free_text=(FreeTextInput(text=text, source=SourceTrace.CHAT_EXPLICIT),),
    )
    unit = dict(
        segment_id="free_text:0",
        text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=None,
        proposed_value=None,
        semantic_kind=SemanticKind.USER_PREFERENCE,
    )
    await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1", event_key="initial-1", input_id=input_id, run_started_at=NOW
        ),
        review_store=store,
        model_gateway=Gateway(
            [SemanticExtractionDraft(units=[
                SemanticUnitDraft(slot_id=4, **unit),
                SemanticUnitDraft(slot_id=5, **unit),
            ])]
        ),
    )
    ask = next(
        record
        for record in await store.get_ask_records("run-1")
        if record.kind.value == "AMBIGUOUS"
    )
    assert ask.schema_version == "ask_record/v3"
    assert ask.issue_slot_ids == (4, 5)
    assert ask.issue_source_key == "free_text:0:0:6"

    reply = "HBM 수요가 핵심 이유입니다"
    source = build_resume_semantic_source(
        "run-1", resume_key="resume-ambiguity", slot_id=ask.slot_id,
        issue_id=ask.issue_id, raw_text=reply
    )
    resumed = await process_intake_review(
        HitlResumeEvent(
            run_id="run-1",
            event_key="resume-ambiguity",
            input_id=input_id,
            ask_id=ask.ask_id,
            raw_answer=reply,
            run_started_at=NOW,
        ),
        review_store=store,
        model_gateway=Gateway([SemanticExtractionDraft(units=[SemanticUnitDraft(
            segment_id=source.segment_id,
            slot_id=ask.slot_id,
            text_span=source.sanitized_text,
            span_offset=(0, len(source.sanitized_text)),
            normalized_proposition=None,
            proposed_value=None,
            semantic_kind=SemanticKind.USER_PREFERENCE,
        )])]),
    )

    assert resumed.issues[0].issue_id == ask.issue_id


def test_AskBackDraft는_target_allowlist_count_duplicate를_fail_closed한다():
    from app.domain.hitl_policy import AskTarget
    from app.domain.missing import MissingKind, MissingReason, RequiredFor

    targets = (
        AskTarget(
            slot_id=2,
            kind=MissingKind.ABSENT,
            priority=90,
            reason=MissingReason.HOLDING_STATE_REQUIRED,
            required_for=(RequiredFor.DECISION_CONTEXT,),
        ),
    )
    assert validate_ask_back_draft(
        AskBackDraft(questions=[AskBackQuestionDraft(slot_id=2, question="보유 중인가요?")]),
        targets,
    ).questions[0].slot_id == 2
    with pytest.raises(ValueError, match="not selected"):
        validate_ask_back_draft(
            AskBackDraft(questions=[AskBackQuestionDraft(slot_id=3, question="기간은?")]),
            targets,
        )
    with pytest.raises(ValueError, match="duplicate"):
        validate_ask_back_draft(
            AskBackDraft(
                questions=[
                    AskBackQuestionDraft(slot_id=2, question="보유 중인가요?"),
                    AskBackQuestionDraft(slot_id=2, question="다시 묻습니다"),
                ]
            ),
            targets,
        )

    second = targets + (
        targets[0].model_copy(update={"slot_id": 3}),
    )
    with pytest.raises(ValueError, match="order"):
        validate_ask_back_draft(
            AskBackDraft(
                questions=[
                    AskBackQuestionDraft(slot_id=3, question="기간은?"),
                    AskBackQuestionDraft(slot_id=2, question="보유 중인가요?"),
                ]
            ),
            second,
        )


@pytest.mark.asyncio
async def test_resume는_AskRecord_target과_issue를_USER_CONFIRMED_observation에_연결한다():
    from app.domain.intake import FreeTextInput

    store = MemoryReviewStore()
    conflict_text = "not holding"
    input_id = await stored_input(
        store,
        structured=(
            answer(1, "CONSIDER_ENTRY"),
            answer(2, "HOLDING"),
            answer(3, "LONG"),
            answer(4, "개인 관심"),
            answer(5, "장기 성장"),
            answer(8, "전제가 바뀌면 재검토"),
        ),
        free_text=(FreeTextInput(text=conflict_text, source=SourceTrace.CHAT_EXPLICIT),),
    )
    conflict_draft = SemanticExtractionDraft(
        units=[
            SemanticUnitDraft(
                segment_id="free_text:0",
                slot_id=2,
                text_span=conflict_text,
                span_offset=(0, len(conflict_text)),
                normalized_proposition=None,
                proposed_value="NOT_HOLDING",
                semantic_kind=SemanticKind.USER_STATE,
            )
        ]
    )
    first = await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1", event_key="initial-1", input_id=input_id, run_started_at=NOW
        ),
        review_store=store,
        model_gateway=Gateway([conflict_draft]),
    )
    ask = next(record for record in await store.get_ask_records("run-1") if record.slot_id == 2)
    reply = "no user@example.com 010-1234-5678"
    # Runtime-owned source identity is deterministic; adapt only the fixture's segment/span.
    from app.domain.resume_source import build_resume_semantic_source

    source = build_resume_semantic_source(
        "run-1", resume_key="resume-1", slot_id=2, issue_id=ask.issue_id, raw_text=reply
    )
    resume_draft = SemanticExtractionDraft(
        units=[
            SemanticUnitDraft(
                segment_id=source.segment_id,
                slot_id=2,
                text_span=source.sanitized_text,
                span_offset=(0, len(source.sanitized_text)),
                normalized_proposition=None,
                proposed_value="NOT_HOLDING",
                semantic_kind=SemanticKind.USER_STATE,
            )
        ]
    )

    resumed = await process_intake_review(
        HitlResumeEvent(
            run_id="run-1",
            event_key="resume-1",
            input_id=input_id,
            ask_id=ask.ask_id,
            raw_answer=reply,
            run_started_at=NOW,
            issues=first.issues,
        ),
        review_store=store,
        model_gateway=Gateway([resume_draft]),
    )

    slot2 = resumed.projections[1]
    assert slot2.values == ("NOT_HOLDING",)
    assert slot2.issue_ids == ()
    sources = await store.get_resume_sources("run-1")
    assert sources[-1].origin is SourceTrace.USER_CONFIRMED
    assert sources[-1].issue_id == ask.issue_id
    assert "user@example.com" not in sources[-1].sanitized_text
    assert 2 not in {item.slot_id for item in resumed.ask_targets}


@pytest.mark.asyncio
async def test_USER_DECLINED_resume는_재질문하지_않고_blocking이면_BLOCKED다():
    store = MemoryReviewStore()
    input_id = await stored_input(
        store,
        structured=(
            answer(1, "CONSIDER_ENTRY"),
            answer(3, "LONG"),
            answer(4, "개인 관심"),
            answer(5, "장기 성장"),
            answer(8, "전제가 바뀌면 재검토"),
        ),
    )
    await process_intake_review(
        InitialIntakeEvent(
            run_id="run-1", event_key="initial-1", input_id=input_id, run_started_at=NOW
        ),
        review_store=store,
        model_gateway=Gateway([SemanticExtractionDraft(units=[])]),
    )
    ask = next(record for record in await store.get_ask_records("run-1") if record.slot_id == 2)

    result = await process_intake_review(
        HitlResumeEvent(
            run_id="run-1",
            event_key="resume-1",
            input_id=input_id,
            ask_id=ask.ask_id,
            raw_answer="답변하지 않겠습니다",
            response_state=ResponseState.USER_DECLINED,
            run_started_at=NOW,
        ),
        review_store=store,
        model_gateway=Gateway([]),
    )

    assert result.routing_outcome is RoutingOutcome.BLOCKED
    assert 2 not in {item.slot_id for item in result.ask_targets}
