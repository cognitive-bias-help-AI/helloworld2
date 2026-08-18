import pytest

from app.domain.semantic_source import (
    SemanticTextRef,
    build_semantic_anchor,
    build_semantic_segments,
    resolve_global_span,
)


def answer(slot_id, value, *, source="survey", response_state="answered"):
    return {
        "slot_id": slot_id,
        "value": value,
        "source": source,
        "response_state": response_state,
    }


def masked_intake(*, structured=(), free_text=(), target=None):
    return {
        "mode": "HYBRID",
        "target": target,
        "structured": list(structured),
        "free_text": list(free_text),
    }


def test_v1_projection은_structured_text와_free_text를_고정_순서로_투영한다():
    body = masked_intake(
        target={
            "selected_code": "005930",
            "name": "삼성전자",
            "market": "KOSPI",
            "source": "survey",
        },
        structured=(
            answer(7, "우려 문장"),
            answer(1, "WAIT"),
            answer(4, "주된 이유"),
            answer(6, ["NEWS"]),
            answer(5, "기대 결과"),
            answer(8, "변경 조건"),
        ),
        free_text=(
            {"text": "첫 자유문", "source": "survey"},
            {"text": "둘째 자유문", "source": "chat_explicit"},
        ),
    )

    segments = build_semantic_segments(body, "semantic_projection/v1")

    assert [item.segment_id for item in segments] == [
        "structured:4",
        "structured:5",
        "structured:7",
        "structured:8",
        "free_text:0",
        "free_text:1",
    ]
    assert [item.locked_slot_id for item in segments] == [4, 5, 7, 8, None, None]
    assert [item.origin.value for item in segments] == [
        "survey",
        "survey",
        "survey",
        "survey",
        "survey",
        "chat_explicit",
    ]
    assert build_semantic_anchor(segments) == (
        "주된 이유\n기대 결과\n우려 문장\n변경 조건\n첫 자유문\n둘째 자유문"
    )
    assert "삼성전자" not in build_semantic_anchor(segments)
    assert "WAIT" not in build_semantic_anchor(segments)
    assert "NEWS" not in build_semantic_anchor(segments)


def test_projection은_unknown_version을_silent_fallback하지_않는다():
    with pytest.raises(ValueError, match="unknown semantic projection version"):
        build_semantic_segments(masked_intake(), "semantic_projection/latest")


def test_projection은_동일_input에서_동일_segments와_anchor를_재생성한다():
    body = masked_intake(
        structured=(answer(4, "HBM 공급 부족"),),
        free_text=({"text": "장기로 본다", "source": "chat_explicit"},),
    )

    first = build_semantic_segments(body, "semantic_projection/v1")
    second = build_semantic_segments(body, "semantic_projection/v1")

    assert first == second
    assert build_semantic_anchor(first) == build_semantic_anchor(second)
    assert build_semantic_anchor(first) == "HBM 공급 부족\n장기로 본다"


def test_projection은_stored_unicode를_추가_normalization하지_않는다():
    decomposed = "e\u0301 전망"
    segments = build_semantic_segments(
        masked_intake(free_text=({"text": decomposed, "source": "chat_explicit"},)),
        "semantic_projection/v1",
    )

    assert segments[0].text == decomposed
    assert len(segments[0].text[:2]) == 2
    assert build_semantic_anchor(segments) == decomposed


def test_local_span은_segment와_global_anchor에_동시에_포함된다():
    segments = build_semantic_segments(
        masked_intake(
            structured=(answer(4, "앞 문장"),),
            free_text=({"text": "장기로 본다", "source": "chat_explicit"},),
        ),
        "semantic_projection/v1",
    )

    offset = resolve_global_span(
        segments,
        SemanticTextRef(segment_id="free_text:0", local_start=0, local_end=3),
        text_span="장기로",
        expected_slot_id=3,
    )
    anchor = build_semantic_anchor(segments)

    assert offset == (5, 8)
    assert anchor[offset[0] : offset[1]] == "장기로"
    assert offset[1] - offset[0] == len("장기로")


@pytest.mark.parametrize(
    "reference,text_span,error",
    [
        (SemanticTextRef(segment_id="free_text:9", local_start=0, local_end=1), "문", "unknown segment"),
        (SemanticTextRef(segment_id="free_text:0", local_start=0, local_end=2), "오답", "span mismatch"),
        (SemanticTextRef(segment_id="free_text:0", local_start=0, local_end=20), "문장", "out of bounds"),
    ],
)
def test_invalid_local_span은_canonical_global_offset으로_승격되지_않는다(
    reference, text_span, error
):
    segments = build_semantic_segments(
        masked_intake(free_text=({"text": "문장", "source": "chat_explicit"},)),
        "semantic_projection/v1",
    )

    with pytest.raises(ValueError, match=error):
        resolve_global_span(
            segments,
            reference,
            text_span=text_span,
            expected_slot_id=4,
        )


def test_structured_segment는_expected_slot_lock_mismatch를_거부한다():
    segments = build_semantic_segments(
        masked_intake(structured=(answer(4, "HBM 수요"),)),
        "semantic_projection/v1",
    )

    with pytest.raises(ValueError, match="locked slot mismatch"):
        resolve_global_span(
            segments,
            SemanticTextRef(segment_id="structured:4", local_start=0, local_end=3),
            text_span="HBM",
            expected_slot_id=7,
        )


def test_segment_boundary와_separator를_넘는_span은_거부한다():
    segments = build_semantic_segments(
        masked_intake(
            structured=(answer(4, "A"),),
            free_text=({"text": "B", "source": "chat_explicit"},),
        ),
        "semantic_projection/v1",
    )

    with pytest.raises(ValueError, match="out of bounds"):
        resolve_global_span(
            segments,
            SemanticTextRef(segment_id="structured:4", local_start=0, local_end=2),
            text_span="A\n",
            expected_slot_id=4,
        )


def test_negative_or_reversed_local_offset은_reference_contract에서_거부한다():
    with pytest.raises(ValueError):
        SemanticTextRef(segment_id="free_text:0", local_start=-1, local_end=1)
    with pytest.raises(ValueError):
        SemanticTextRef(segment_id="free_text:0", local_start=2, local_end=2)


def test_emoji_offset은_Python_Unicode_code_point_단위다():
    segments = build_semantic_segments(
        masked_intake(
            structured=(answer(4, "X"),),
            free_text=({"text": "A😀B", "source": "chat_explicit"},),
        ),
        "semantic_projection/v1",
    )

    offset = resolve_global_span(
        segments,
        SemanticTextRef(segment_id="free_text:0", local_start=1, local_end=2),
        text_span="😀",
        expected_slot_id=3,
    )

    assert build_semantic_anchor(segments) == "X\nA😀B"
    assert offset == (3, 4)
    assert build_semantic_anchor(segments)[offset[0] : offset[1]] == "😀"
