import inspect
from itertools import permutations

import pytest
from pydantic import ValidationError

from app.domain.intake import ResponseState
from app.domain.missing import MissingKind, analyze_missing
from app.domain.semantic_source import SemanticTextRef
from app.domain.slot_context import ExtractionMethod, build_slot_observation
from app.domain.slot_resolution import (
    CurrentSlotStatus,
    HydratedSlotObservation,
    ResolutionIssue,
    build_ambiguity_issue,
    resolve_current_slots,
    to_missing_observations,
)
from app.schemas.frozen import SourceTrace


def hydrated(
    n: int,
    *,
    slot_id: int,
    value: str | tuple[str, ...] | None,
    origin: SourceTrace = SourceTrace.SURVEY,
    text: str | None = None,
    response_state: ResponseState = ResponseState.ANSWERED,
    resolves_issue_ids: tuple[str, ...] = (),
) -> HydratedSlotObservation:
    is_text = slot_id in {4, 5, 7, 8} and response_state is ResponseState.ANSWERED
    observation = build_slot_observation(
        f"run-{n}",
        slot_id=slot_id,
        response_state=response_state,
        origin=origin,
        extraction_method=ExtractionMethod.DIRECT,
        value=None if is_text else value,
        text_ref=(
            SemanticTextRef(
                segment_id=f"segment:{n}",
                local_start=0,
                local_end=len(text or ""),
            )
            if is_text
            else None
        ),
    )
    return HydratedSlotObservation(
        observation=observation,
        text=text,
        resolves_issue_ids=resolves_issue_ids,
    )


def by_slot(result):
    return {item.slot_id: item for item in result}


def test_observation이_하나여도_정확히_8개_projection과_ABSENT를_만든다():
    result = resolve_current_slots([hydrated(1, slot_id=1, value="CONSIDER_ENTRY")])

    assert [item.slot_id for item in result] == list(range(1, 9))
    assert result[0].status is CurrentSlotStatus.RESOLVED
    assert all(item.status is CurrentSlotStatus.ABSENT for item in result[1:])


def test_enum은_같은_값이면_agreement이고_다른_값이면_conflict다():
    agreement = resolve_current_slots(
        [
            hydrated(1, slot_id=2, value="HOLDING"),
            hydrated(
                2,
                slot_id=2,
                value="HOLDING",
                origin=SourceTrace.CHAT_EXPLICIT,
            ),
        ]
    )[1]
    conflict = resolve_current_slots(
        [
            hydrated(1, slot_id=2, value="HOLDING"),
            hydrated(
                3,
                slot_id=2,
                value="NOT_HOLDING",
                origin=SourceTrace.CHAT_EXPLICIT,
            ),
        ]
    )[1]

    assert (agreement.status, agreement.values) == (
        CurrentSlotStatus.RESOLVED,
        ("HOLDING",),
    )
    assert conflict.status is CurrentSlotStatus.CONFLICT
    assert conflict.values == ("HOLDING", "NOT_HOLDING")
    assert len(conflict.issue_ids) == 1


def test_categories는_set_merge하고_NONE_CHECKED_혼합은_conflict다():
    merged = resolve_current_slots(
        [
            hydrated(1, slot_id=6, value=("FINANCIALS",)),
            hydrated(
                2,
                slot_id=6,
                value=("NEWS",),
                origin=SourceTrace.CHAT_EXPLICIT,
            ),
        ]
    )[5]
    contradicted = resolve_current_slots(
        [
            hydrated(3, slot_id=6, value=("NONE_CHECKED",)),
            hydrated(4, slot_id=6, value=("NEWS",)),
        ]
    )[5]

    assert (merged.status, merged.values) == (
        CurrentSlotStatus.RESOLVED,
        ("FINANCIALS", "NEWS"),
    )
    assert contradicted.status is CurrentSlotStatus.CONFLICT
    assert contradicted.values == ("NEWS", "NONE_CHECKED")


def test_text는_서로_달라도_additive이고_모든_lineage를_보존한다():
    items = [
        hydrated(1, slot_id=4, value=None, text="AI 성장"),
        hydrated(
            2,
            slot_id=4,
            value=None,
            text="HBM 수요",
            origin=SourceTrace.CHAT_EXPLICIT,
        ),
    ]

    result = resolve_current_slots(items)[3]

    assert result.status is CurrentSlotStatus.RESOLVED
    assert result.values == ("AI 성장", "HBM 수요")
    assert result.observation_ids == tuple(
        sorted(item.observation.observation_id for item in items)
    )
    assert result.issue_ids == ()


def test_text_hydration은_pure_input_boundary이고_Store를_import하지_않는다():
    with pytest.raises(ValidationError, match="hydrated text"):
        HydratedSlotObservation(
            observation=hydrated(1, slot_id=4, value=None, text="근거").observation,
            text=None,
        )

    import app.domain.slot_resolution as module

    source = inspect.getsource(module)
    assert "app.store" not in source
    assert "ReviewStore" not in source


def test_semantic_ambiguity는_conflict로_바꾸지_않고_관련_slot에_보존한다():
    issue = build_ambiguity_issue(slot_ids=(4, 5), source_key="free_text:0:0:8")

    result = by_slot(resolve_current_slots([], issues=[issue]))
    missing = analyze_missing(to_missing_observations(result.values()))

    assert result[4].status is CurrentSlotStatus.AMBIGUOUS
    assert result[5].status is CurrentSlotStatus.AMBIGUOUS
    assert result[4].issue_ids == result[5].issue_ids == (issue.issue_id,)
    assert {item.kind for item in missing if item.slot_id in {4, 5}} == {MissingKind.AMBIGUOUS}


def test_resolution_issue는_canonical_body와_다른_임의_ID를_거부한다():
    issue = build_ambiguity_issue(slot_ids=(4, 5), source_key="free_text:0:0:8")

    with pytest.raises(ValidationError, match="deterministic issue_id"):
        ResolutionIssue(**(issue.model_dump() | {"issue_id": "random-issue"}))


def test_USER_CONFIRMED는_target_issue가_있을_때만_해당_conflict를_해소한다():
    original = [
        hydrated(1, slot_id=2, value="HOLDING"),
        hydrated(
            2,
            slot_id=2,
            value="NOT_HOLDING",
            origin=SourceTrace.CHAT_EXPLICIT,
        ),
    ]
    conflict = resolve_current_slots(original)[1]
    untargeted = hydrated(
        3,
        slot_id=2,
        value="NOT_HOLDING",
        origin=SourceTrace.USER_CONFIRMED,
    )
    targeted = hydrated(
        4,
        slot_id=2,
        value="NOT_HOLDING",
        origin=SourceTrace.USER_CONFIRMED,
        resolves_issue_ids=conflict.issue_ids,
    )

    assert resolve_current_slots([*original, untargeted])[1].status is CurrentSlotStatus.CONFLICT
    resolved = resolve_current_slots([*original, targeted])[1]
    assert (resolved.status, resolved.values, resolved.issue_ids) == (
        CurrentSlotStatus.RESOLVED,
        ("NOT_HOLDING",),
        (),
    )


def test_targeted_resolution은_해당_issue만_해소하고_unrelated_text를_보존한다():
    issue = build_ambiguity_issue(slot_ids=(4, 5), source_key="free_text:0:0:8")
    existing = hydrated(1, slot_id=4, value=None, text="기존 이유")
    confirmed = hydrated(
        2,
        slot_id=4,
        value=None,
        text="확인된 이유",
        origin=SourceTrace.USER_CONFIRMED,
        resolves_issue_ids=(issue.issue_id,),
    )

    result = by_slot(resolve_current_slots([existing, confirmed], issues=[issue]))

    assert (result[4].status, result[4].values, result[4].issue_ids) == (
        CurrentSlotStatus.RESOLVED,
        ("기존 이유", "확인된 이유"),
        (),
    )
    assert result[5].status is CurrentSlotStatus.ABSENT


def test_resolution은_SourceTrace_선언순서와_입력순서에_독립적이다():
    items = [
        hydrated(1, slot_id=2, value="HOLDING"),
        hydrated(
            2,
            slot_id=2,
            value="NOT_HOLDING",
            origin=SourceTrace.CHAT_EXPLICIT,
        ),
    ]

    outputs = {
        tuple(item.model_dump_json() for item in resolve_current_slots(order))
        for order in permutations(items)
    }

    assert len(outputs) == 1


def test_non_answered_state는_ABSENT_projection과_Missing_policy에_보존된다():
    declined = hydrated(
        1,
        slot_id=1,
        value=None,
        response_state=ResponseState.USER_DECLINED,
    )

    projection = resolve_current_slots([declined])[0]
    missing = analyze_missing(to_missing_observations([projection]))[0]

    assert projection.status is CurrentSlotStatus.ABSENT
    assert projection.response_state is ResponseState.USER_DECLINED
    assert missing.response_state is ResponseState.USER_DECLINED
