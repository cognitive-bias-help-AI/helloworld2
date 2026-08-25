"""Deterministic local span re-anchoring for semantic model drafts."""

from __future__ import annotations

from app.domain.semantic_source import SemanticTextSegment
from app.orchestration.drafts import SemanticExtractionDraft


def _candidates(text: str, span: str) -> list[int]:
    if not span:
        return []
    result: list[int] = []
    cursor = 0
    while True:
        index = text.find(span, cursor)
        if index < 0:
            return result
        result.append(index)
        cursor = index + 1


def reanchor_semantic_draft(
    draft: SemanticExtractionDraft,
    segments: tuple[SemanticTextSegment, ...],
) -> SemanticExtractionDraft:
    """Return a copy with only uniquely resolved local offsets replaced.

    The model's ``text_span`` remains authoritative input to the existing
    assembler; this boundary never normalizes text or changes segment/slot
    ownership. Ambiguous matches are left untouched so validation fails closed.
    """
    by_id = {segment.segment_id: segment for segment in segments}
    units = []
    for unit in draft.units:
        segment = by_id.get(unit.segment_id)
        if segment is None:
            units.append(unit)
            continue
        matches = _candidates(segment.text, unit.text_span)
        selected: int | None = None
        if len(matches) == 1:
            selected = matches[0]
        elif len(matches) > 1:
            distances = [abs(index - unit.span_offset[0]) for index in matches]
            best = min(distances)
            if distances.count(best) == 1:
                selected = matches[distances.index(best)]
        if selected is None:
            units.append(unit)
        else:
            units.append(unit.model_copy(update={
                "span_offset": (selected, selected + len(unit.text_span)),
            }))
    return draft.model_copy(update={"units": units})


__all__ = ["reanchor_semantic_draft"]
