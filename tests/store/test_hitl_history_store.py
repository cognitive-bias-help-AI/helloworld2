import asyncio

import pytest

from app.domain.ask_history import build_ask_record
from app.domain.hitl_policy import AskTarget
from app.domain.missing import MissingKind, MissingReason, RequiredFor
from app.domain.resume_source import build_resume_semantic_source
from app.store.memory_review_store import MemoryReviewStore

run = asyncio.run


def target(slot_id=8):
    return AskTarget(
        slot_id=slot_id,
        kind=MissingKind.ABSENT,
        priority=50,
        reason=MissingReason.CHANGE_CONDITION_MISSING,
        required_for=(RequiredFor.DECISION_CONTEXT,),
    )


def test_resume_source_append_exact_replay와_payload_conflict():
    store = MemoryReviewStore()
    input_id = run(store.put_input("run-1", {"masked_input": "최초 입력"}))
    source = build_resume_semantic_source(
        "run-1", resume_key="resume-1", slot_id=8, raw_text="감소하면 재검토"
    )

    assert run(store.put_resume_sources("run-1", [source])) == [source.source_id]
    assert run(store.put_resume_sources("run-1", [source])) == [source.source_id]
    assert run(store.get_resume_sources("run-1")) == [source]
    assert run(store.get_input(input_id)) == {"masked_input": "최초 입력"}

    changed = source.model_copy(update={"sanitized_text": "증가하면 재검토"})
    with pytest.raises(ValueError, match="resume source ownership/payload conflict"):
        run(store.put_resume_sources("run-1", [changed]))
    with pytest.raises(ValueError, match="resume source ownership/payload conflict"):
        run(store.put_resume_sources("other", [source]))


def test_ask_history_append_exact_replay와_payload_conflict():
    store = MemoryReviewStore()
    record = build_ask_record(
        "run-1", ask_key="ask-1", target=target(), issue_id="issue-8", sequence=1
    )

    assert run(store.put_ask_records("run-1", [record])) == [record.ask_id]
    assert run(store.put_ask_records("run-1", [record])) == [record.ask_id]
    assert run(store.get_ask_records("run-1")) == [record]

    changed = record.model_copy(update={"sequence": 2})
    with pytest.raises(ValueError, match="ask record ownership/payload conflict"):
        run(store.put_ask_records("run-1", [changed]))


def test_conflicting_batch는_partial_write를_남기지_않는다():
    store = MemoryReviewStore()
    existing = build_resume_semantic_source(
        "run-1", resume_key="resume-1", slot_id=8, raw_text="기존"
    )
    run(store.put_resume_sources("run-1", [existing]))
    new = build_resume_semantic_source(
        "run-1", resume_key="resume-2", slot_id=8, raw_text="신규"
    )
    conflict = existing.model_copy(update={"sanitized_text": "변조"})

    with pytest.raises(ValueError):
        run(store.put_resume_sources("run-1", [new, conflict]))
    assert run(store.get_resume_sources("run-1")) == [existing]
