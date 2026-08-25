from app.domain.semantic import SemanticKind
from app.domain.semantic_source import SemanticTextSegment
from app.orchestration.drafts import SemanticExtractionDraft, SemanticUnitDraft
from app.orchestration.span_anchoring import reanchor_semantic_draft
from app.schemas.frozen import SourceTrace


def segment(text: str, *, locked_slot_id: int | None = None):
    return SemanticTextSegment(
        segment_id="free_text:0", origin=SourceTrace.CHAT_EXPLICIT,
        locked_slot_id=locked_slot_id, text=text, anchor_start=0, anchor_end=len(text),
    )


def draft(text_span: str, offset: tuple[int, int], *, locked_slot_id: int | None = None):
    return SemanticExtractionDraft(units=[SemanticUnitDraft(
        segment_id="free_text:0", slot_id=locked_slot_id or 4, text_span=text_span,
        span_offset=offset, normalized_proposition=text_span,
        proposed_value=None, semantic_kind=SemanticKind.EXTERNAL_ASSERTION,
    )])


def test_unique_exact_span_reanchors_off_by_one_and_wild_offset():
    source = segment("삼성전자 영업이익이 증가했다")
    assert reanchor_semantic_draft(draft("영업이익이 증가했다", (5, 14)), (source,)).units[0].span_offset == (5, 15)
    assert reanchor_semantic_draft(draft("영업이익이 증가했다", (0, 9)), (source,)).units[0].span_offset == (5, 15)


def test_absent_span_is_unchanged_for_assembler_fail_closed():
    source = segment("삼성전자 실적")
    result = reanchor_semantic_draft(draft("영업이익", (0, 4)), (source,))
    assert result == draft("영업이익", (0, 4))


def test_duplicate_span_uses_unique_nearest_advisory_hint_but_tie_stays_closed():
    source = segment("HBM 수요와 HBM 수요")
    resolved = reanchor_semantic_draft(draft("HBM 수요", (10, 15)), (source,))
    assert resolved.units[0].span_offset == (8, 14)
    tied = reanchor_semantic_draft(draft("HBM 수요", (4, 12)), (source,))
    assert tied.units[0].span_offset == (4, 12)


def test_locked_slot_id_is_preserved():
    source = segment("영업이익이 증가했다", locked_slot_id=4)
    result = reanchor_semantic_draft(draft("영업이익이 증가했다", (1, 9), locked_slot_id=4), (source,))
    assert source.locked_slot_id == 4
    assert result.units[0].slot_id == 4
