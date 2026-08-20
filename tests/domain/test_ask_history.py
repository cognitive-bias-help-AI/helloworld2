import pytest

from app.domain.ask_history import (
    AskRecord,
    build_ask_record,
    project_hitl_context,
    reconstruct_ambiguity_issue,
)
from app.domain.hitl_policy import AskTarget
from app.domain.missing import MissingKind, MissingReason, RequiredFor
from app.domain.slot_resolution import build_ambiguity_issue


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


def test_v1_payload는_backward_compatible_read되고_builder는_v2를_생성한다():
    v1 = AskRecord.model_validate(
        {
            "schema_version": "ask_record/v1",
            "ask_id": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
            "ask_key": "legacy-ask",
            "slot_id": 4,
            "kind": "ABSENT",
            "reason": "PRIMARY_REASON_REQUIRED",
            "sequence": 0,
        }
    )
    v2 = build_ask_record("run-1", ask_key="ask-v2", target=target(4), sequence=1)

    assert v1.schema_version == "ask_record/v1"
    assert v2.schema_version == "ask_record/v2"


def test_v2_ambiguity는_minimal_lineage로_issue를_exact_reconstruct한다():
    issue = build_ambiguity_issue(
        slot_ids=(4, 5), source_key="free_text:0:0:8"
    )
    record = build_ask_record(
        "run-1",
        ask_key="ask-ambiguity",
        target=target(4).model_copy(update={"kind": MissingKind.AMBIGUOUS}),
        issue_id=issue.issue_id,
        issue_slot_ids=issue.slot_ids,
        issue_source_key=issue.source_key,
        sequence=1,
    )

    assert record.issue_slot_ids == (4, 5)
    assert reconstruct_ambiguity_issue(record) == issue


@pytest.mark.parametrize(
    "update",
    [
        {"issue_id": None, "issue_slot_ids": (4,), "issue_source_key": "source"},
        {"issue_id": "issue", "issue_slot_ids": (), "issue_source_key": None},
        {"issue_slot_ids": (5, 4), "issue_source_key": "free_text:0:0:8"},
        {"issue_slot_ids": (5,), "issue_source_key": "free_text:0:0:8"},
        {"issue_slot_ids": (4, 5), "issue_source_key": "wrong-source"},
    ],
)
def test_v2_ambiguity_lineage_mismatch는_fail_closed한다(update):
    issue = build_ambiguity_issue(
        slot_ids=(4, 5), source_key="free_text:0:0:8"
    )
    body = {
        "schema_version": "ask_record/v2",
        "ask_id": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
        "ask_key": "ask-ambiguity",
        "slot_id": 4,
        "issue_id": issue.issue_id,
        "issue_slot_ids": (4, 5),
        "issue_source_key": issue.source_key,
        "kind": "AMBIGUOUS",
        "reason": "SLOT_AMBIGUOUS",
        "sequence": 0,
    }
    with pytest.raises(ValueError):
        AskRecord.model_validate(body | update)


def test_conflict는_issue_body를_저장하지_않는다():
    record = build_ask_record(
        "run-1",
        ask_key="ask-conflict",
        target=target(2),
        issue_id="slot_issue_conflict",
        sequence=1,
    )

    assert record.issue_slot_ids == ()
    assert record.issue_source_key is None
