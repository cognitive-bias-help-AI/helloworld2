import pytest

from app.domain.resume_source import (
    ResumeSemanticSource,
    build_resume_segment,
    build_resume_semantic_source,
    build_resume_text_ref,
)
from app.schemas.frozen import SourceTrace


def test_resume_source는_PII를_sanitize하고_target_lineage를_보존한다():
    source = build_resume_semantic_source(
        "run-1",
        resume_key="checkpoint-7",
        slot_id=8,
        issue_id="slot_issue_abc",
        raw_text="  영업이익 user@example.com 010-1234-5678 감소  ",
    )

    assert source.origin is SourceTrace.USER_CONFIRMED
    assert source.sanitized_text == "영업이익 [EMAIL] [PHONE] 감소"
    assert source.slot_id == 8
    assert source.issue_id == "slot_issue_abc"
    assert source.semantic_projection_version == "semantic_projection/v1"
    assert "user@example.com" not in source.model_dump_json()


def test_resume_source_identity와_segment_text_ref는_stable하다():
    kwargs = dict(
        resume_key="checkpoint-7",
        slot_id=8,
        issue_id="slot_issue_abc",
        raw_text="영업이익이 감소하면 다시 보겠습니다.",
    )
    first = build_resume_semantic_source("run-1", **kwargs)
    second = build_resume_semantic_source("run-1", **kwargs)

    assert first == second
    segment = build_resume_segment(first, anchor_start=12)
    reference = build_resume_text_ref(first)
    assert segment.segment_id == first.segment_id
    assert segment.origin is SourceTrace.USER_CONFIRMED
    assert segment.locked_slot_id == 8
    assert segment.anchor_start == 12
    assert segment.anchor_end == 12 + len(first.sanitized_text)
    assert reference.segment_id == first.segment_id
    assert (reference.local_start, reference.local_end) == (
        0,
        len(first.sanitized_text),
    )


def test_resume_identity는_payload와_분리되어_conflicting_replay를_검출할_수_있다():
    first = build_resume_semantic_source(
        "run-1", resume_key="resume-1", slot_id=2, raw_text="아니요"
    )
    changed = build_resume_semantic_source(
        "run-1", resume_key="resume-1", slot_id=2, raw_text="네"
    )

    assert first.source_id == changed.source_id
    assert first != changed


def test_resume_source는_blank_identity와_text를_거부한다():
    with pytest.raises(ValueError):
        build_resume_semantic_source(
            "run-1", resume_key=" ", slot_id=2, raw_text="아니요"
        )
    with pytest.raises(ValueError):
        build_resume_semantic_source(
            "run-1", resume_key="resume-1", slot_id=2, raw_text="  "
        )


def test_resume_source_model은_builder를_우회한_raw_PII를_거부한다():
    valid = build_resume_semantic_source(
        "run-1", resume_key="resume-1", slot_id=2, raw_text="아니요"
    )
    with pytest.raises(ValueError, match="must already be sanitized"):
        ResumeSemanticSource(**valid.model_dump(exclude={"sanitized_text"}), sanitized_text="a@b.com")
