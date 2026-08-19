from app.domain.ask_history import (
    build_ask_record,
    project_hitl_context,
)
from app.domain.hitl_policy import AskTarget
from app.domain.missing import MissingKind, MissingReason, RequiredFor


def target(slot_id: int) -> AskTarget:
    return AskTarget(
        slot_id=slot_id,
        kind=MissingKind.CONFLICT,
        priority=120,
        reason=MissingReason.SLOT_CONFLICT,
        required_for=(RequiredFor.CONFLICT_RESOLUTION,),
    )


def test_ask_record는_issue_aware_lineage와_stable_identity를_보존한다():
    first = build_ask_record(
        "run-1",
        ask_key="turn-2:question-1",
        target=target(4),
        issue_id="slot_issue_a",
        sequence=3,
    )
    second = build_ask_record(
        "run-1",
        ask_key="turn-2:question-1",
        target=target(4),
        issue_id="slot_issue_a",
        sequence=3,
    )

    assert first == second
    assert first.slot_id == 4
    assert first.issue_id == "slot_issue_a"
    assert first.kind is MissingKind.CONFLICT
    assert first.reason is MissingReason.SLOT_CONFLICT


def test_ask_history_projection은_순서와_duplicate에_독립적이다():
    slot2 = build_ask_record(
        "run-1", ask_key="ask-2", target=target(2), sequence=2
    )
    slot8 = build_ask_record(
        "run-1", ask_key="ask-8", target=target(8), sequence=1
    )

    assert project_hitl_context([slot8, slot2, slot8]).already_asked_slot_ids == (2, 8)
    assert project_hitl_context([slot2, slot8]).already_asked_slot_ids == (2, 8)


def test_같은_slot의_서로_다른_issue를_record에서_구분한다():
    ambiguity = build_ask_record(
        "run-1",
        ask_key="ask-a",
        target=target(4),
        issue_id="slot_issue_ambiguity",
        sequence=1,
    )
    conflict = build_ask_record(
        "run-1",
        ask_key="ask-b",
        target=target(4),
        issue_id="slot_issue_conflict",
        sequence=2,
    )

    assert ambiguity.issue_id != conflict.issue_id
    assert project_hitl_context([ambiguity, conflict]).already_asked_slot_ids == (4,)
