import pytest
from pydantic import ValidationError

from app.domain.intake import ResponseState
from app.domain.missing import (
    MissingKind,
    MissingReason,
    RequiredFor,
    SlotObservation,
    analyze_missing,
)

FILLED_VALUES = {
    1: "WAIT",
    2: "NOT_HOLDING",
    3: "LONG",
    4: "HBM 수요 증가",
    5: "실적 개선",
    6: ("NEWS",),
    7: "주가 상승 부담",
    8: "수요가 감소하면 재검토",
}


def filled(slot_id: int, value=None) -> SlotObservation:
    return SlotObservation(
        slot_id=slot_id,
        value=FILLED_VALUES[slot_id] if value is None else value,
        response_state=ResponseState.ANSWERED,
    )


def unknown(slot_id: int, **changes) -> SlotObservation:
    values = {
        "slot_id": slot_id,
        "value": None,
        "response_state": ResponseState.UNKNOWN,
    }
    values.update(changes)
    return SlotObservation(**values)


def all_filled() -> tuple[SlotObservation, ...]:
    return tuple(filled(slot_id) for slot_id in range(1, 9))


def one_missing(slot_id: int, **changes):
    observations = list(all_filled())
    observations[slot_id - 1] = unknown(slot_id, **changes)
    return analyze_missing(observations)


def test_모든_8개_Slot이_filled이면_Missing이_없다():
    assert analyze_missing(all_filled()) == ()


@pytest.mark.parametrize(
    "slot_id,blocking,askable,reason",
    [
        (1, True, True, MissingReason.ACTION_REQUIRED),
        (2, True, True, MissingReason.HOLDING_STATE_REQUIRED),
        (3, False, True, MissingReason.HORIZON_UNKNOWN),
        (4, True, True, MissingReason.PRIMARY_REASON_REQUIRED),
        (5, False, True, MissingReason.EXPECTED_OUTCOME_MISSING),
        (6, False, False, MissingReason.INFORMATION_CHECKED_MISSING),
        (7, False, False, MissingReason.COUNTER_EVIDENCE_MISSING),
        (8, False, True, MissingReason.CHANGE_CONDITION_MISSING),
    ],
)
def test_absent_Slot은_registry_policy와_stable_reason을_사용한다(
    slot_id, blocking, askable, reason
):
    result = one_missing(slot_id)

    assert len(result) == 1
    assert result[0].slot_id == slot_id
    assert result[0].kind is MissingKind.ABSENT
    assert result[0].blocking is blocking
    assert result[0].askable is askable
    assert result[0].reason is reason


def test_S3_UNDECIDED_answered는_Missing이_아니다():
    observations = list(all_filled())
    observations[2] = filled(3, "UNDECIDED")

    assert analyze_missing(observations) == ()


def test_USER_DECLINED는_Missing으로_보존하고_policy가_재질문을_결정한다():
    result = one_missing(8, response_state=ResponseState.USER_DECLINED)

    assert result[0].kind is MissingKind.ABSENT
    assert result[0].response_state is ResponseState.USER_DECLINED


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"has_conflict": True, "is_ambiguous": True, "is_partial": True}, MissingKind.CONFLICT),
        ({"is_ambiguous": True, "is_partial": True}, MissingKind.AMBIGUOUS),
        ({"is_partial": True}, MissingKind.PARTIAL),
        ({}, MissingKind.ABSENT),
    ],
)
def test_Missing_kind_precedence는_conflict_ambiguous_partial_absent_순이다(
    changes, expected
):
    result = one_missing(3, **changes)

    assert result[0].kind is expected


def test_conflict는_값이_있어도_Missing으로_분류한다():
    observations = list(all_filled())
    observations[2] = SlotObservation(
        slot_id=3,
        value="LONG",
        response_state=ResponseState.ANSWERED,
        has_conflict=True,
    )

    result = analyze_missing(observations)

    assert result[0].kind is MissingKind.CONFLICT
    assert result[0].reason is MissingReason.SLOT_CONFLICT
    assert result[0].required_for == (RequiredFor.CONFLICT_RESOLUTION,)


def test_analyzer는_동일_slot_observation_중복을_거부한다():
    with pytest.raises(ValueError, match="duplicate slot_id"):
        analyze_missing([filled(1), filled(1)])


def test_observation은_extra와_mutation을_거부한다():
    with pytest.raises(ValidationError):
        SlotObservation(
            slot_id=1,
            value="WAIT",
            response_state=ResponseState.ANSWERED,
            extra_field=True,
        )
    value = filled(1)
    with pytest.raises(ValidationError):
        value.is_partial = True


def test_analyzer_result와_JSON은_결정적이다():
    observations = [unknown(8), unknown(1), unknown(3)]

    first = analyze_missing(observations)
    second = analyze_missing(reversed(observations))

    assert first == second
    assert [item.slot_id for item in first] == [1, 3, 8]
    assert [item.model_dump_json() for item in first] == [
        item.model_dump_json() for item in second
    ]

