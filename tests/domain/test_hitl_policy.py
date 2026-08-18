import pytest
from pydantic import ValidationError

from app.domain.hitl_policy import (
    MAX_ASK_PER_TURN,
    AskTarget,
    HitlContext,
    select_ask_targets,
)
from app.domain.intake import ResponseState
from app.domain.missing import MissingInformation, MissingKind, analyze_missing
from tests.domain.test_missing import all_filled, unknown


def missing_for(
    *slot_ids: int, changes: dict[int, dict] | None = None
) -> tuple[MissingInformation, ...]:
    observations = list(all_filled())
    for slot_id in slot_ids:
        per_slot = (changes or {}).get(slot_id, {})
        observations[slot_id - 1] = unknown(slot_id, **per_slot)
    return analyze_missing(observations)


def test_MAX_ASK_PER_TURN은_2이고_5개_후보도_2개만_선택한다():
    result = select_ask_targets(missing_for(1, 2, 3, 6, 8))

    assert MAX_ASK_PER_TURN == 2
    assert [item.slot_id for item in result] == [1, 2]


def test_conflict가_blocking_absent보다_먼저_선택된다():
    missing = missing_for(1, 2, 3, changes={3: {"has_conflict": True}})

    result = select_ask_targets(missing)

    assert [item.slot_id for item in result] == [3, 1]
    assert result[0].kind is MissingKind.CONFLICT


@pytest.mark.parametrize("slot_id", [6, 7])
def test_usually_skip_Slot만_Missing이면_질문하지_않는다(slot_id):
    assert select_ask_targets(missing_for(slot_id)) == ()


def test_S8_only_missing은_once_recommended_질문후보다():
    result = select_ask_targets(missing_for(8))

    assert [item.slot_id for item in result] == [8]


def test_already_asked_S8은_반복_질문하지_않는다():
    context = HitlContext(already_asked_slot_ids=(8,))

    assert select_ask_targets(missing_for(8), context) == ()


@pytest.mark.parametrize(
    "response_state",
    [ResponseState.USER_DECLINED, ResponseState.UNDECIDED],
)
def test_declined와_undecided_response는_질문대상에서_제외한다(response_state):
    missing = missing_for(8, changes={8: {"response_state": response_state}})

    assert select_ask_targets(missing) == ()


def test_S3_canonical_UNDECIDED_answered는_Missing도_AskTarget도_없다():
    observations = list(all_filled())
    observations[2] = observations[2].model_copy(update={"value": "UNDECIDED"})

    assert select_ask_targets(analyze_missing(observations)) == ()


def test_blocking_missing은_optional_missing보다_우선한다():
    result = select_ask_targets(missing_for(2, 5, 8))

    assert [item.slot_id for item in result] == [2, 8]


def test_동일_rank와_priority에서는_slot_id_오름차순이다():
    first = MissingInformation(
        **missing_for(5)[0].model_dump(exclude={"slot_id", "priority"}),
        slot_id=5,
        priority=50,
    )
    second = MissingInformation(
        **missing_for(8)[0].model_dump(exclude={"slot_id", "priority"}),
        slot_id=8,
        priority=50,
    )

    result = select_ask_targets([second, first])

    assert [item.slot_id for item in result] == [5, 8]


def test_Acceptance_turn1과_resolved_turn2가_결정적이다():
    turn1_missing = missing_for(1, 2, 3, 5, 6, 7, 8)
    turn1 = select_ask_targets(turn1_missing)
    turn2_missing = missing_for(3, 5, 6, 7, 8)
    turn2 = select_ask_targets(
        turn2_missing, HitlContext(already_asked_slot_ids=(1, 2))
    )

    assert [item.slot_id for item in turn1] == [1, 2]
    assert [item.slot_id for item in turn2] == [3, 8]
    assert 6 not in {item.slot_id for item in (*turn1, *turn2)}
    assert 7 not in {item.slot_id for item in (*turn1, *turn2)}


def test_HitlContext와_AskTarget은_extra_mutation_duplicate를_거부한다():
    with pytest.raises(ValidationError, match="duplicate already-asked"):
        HitlContext(already_asked_slot_ids=(8, 8))
    with pytest.raises(ValidationError):
        HitlContext(extra_field=True)

    target = select_ask_targets(missing_for(1))[0]
    assert isinstance(target, AskTarget)
    with pytest.raises(ValidationError):
        target.priority = 0


def test_selection은_입력순서와_무관하게_결정적이다():
    missing = missing_for(1, 2, 3, 5, 8)

    assert select_ask_targets(missing) == select_ask_targets(reversed(missing))
