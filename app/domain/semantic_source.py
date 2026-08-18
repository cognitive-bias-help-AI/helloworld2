"""Versioned semantic-text projection and span conversion pure contracts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.intake import HybridIntake, ResponseState
from app.domain.slots import get_slot_definition
from app.schemas.frozen import NonBlankStr, SlotId, SourceTrace

SEMANTIC_PROJECTION_VERSION = "semantic_projection/v1"
SemanticProjectionVersion = Literal["semantic_projection/v1"]
SEMANTIC_ANCHOR_SEPARATOR = "\n"


class _SemanticSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class SemanticTextRef(_SemanticSourceModel):
    segment_id: NonBlankStr
    local_start: int = Field(ge=0)
    local_end: int = Field(gt=0)

    @model_validator(mode="after")
    def enforce_forward_span(self):
        if self.local_end <= self.local_start:
            raise ValueError("local_end must be greater than local_start")
        return self


class SemanticTextSegment(_SemanticSourceModel):
    segment_id: NonBlankStr
    origin: SourceTrace
    locked_slot_id: SlotId | None = None
    text: NonBlankStr
    anchor_start: int = Field(ge=0)
    anchor_end: int = Field(gt=0)

    @model_validator(mode="after")
    def enforce_anchor_extent(self):
        if self.anchor_end - self.anchor_start != len(self.text):
            raise ValueError("segment anchor extent must equal Python text length")
        return self


def _validate_projection_version(version: str) -> SemanticProjectionVersion:
    if version != SEMANTIC_PROJECTION_VERSION:
        raise ValueError(f"unknown semantic projection version: {version}")
    return SEMANTIC_PROJECTION_VERSION


def build_semantic_segments(
    masked_intake: dict,
    projection_version: str,
) -> tuple[SemanticTextSegment, ...]:
    """Derive stable text segments from the sanitized masked-intake body."""

    _validate_projection_version(projection_version)
    intake = HybridIntake.model_validate(
        {"schema_version": "hybrid_intake/v1", **masked_intake}
    )
    sources: list[tuple[str, SourceTrace, int | None, str]] = []
    for item in sorted(intake.structured, key=lambda answer: answer.slot_id):
        definition = get_slot_definition(item.slot_id)
        if (
            item.response_state is ResponseState.ANSWERED
            and definition.value_shape == "text"
        ):
            sources.append(
                (f"structured:{item.slot_id}", item.source, item.slot_id, item.value)
            )
    sources.extend(
        (f"free_text:{index}", item.source, None, item.text)
        for index, item in enumerate(intake.free_text)
    )

    result: list[SemanticTextSegment] = []
    cursor = 0
    for segment_id, origin, locked_slot_id, text in sources:
        end = cursor + len(text)
        result.append(
            SemanticTextSegment(
                segment_id=segment_id,
                origin=origin,
                locked_slot_id=locked_slot_id,
                text=text,
                anchor_start=cursor,
                anchor_end=end,
            )
        )
        cursor = end + len(SEMANTIC_ANCHOR_SEPARATOR)
    return tuple(result)


def build_semantic_anchor(segments: Iterable[SemanticTextSegment]) -> str:
    """Join validated segments without storing another canonical text body."""

    items = tuple(segments)
    expected_start = 0
    seen: set[str] = set()
    for item in items:
        if item.segment_id in seen:
            raise ValueError(f"duplicate segment_id: {item.segment_id}")
        seen.add(item.segment_id)
        if item.anchor_start != expected_start:
            raise ValueError("segment anchor positions are not contiguous")
        expected_start = item.anchor_end + len(SEMANTIC_ANCHOR_SEPARATOR)
    return SEMANTIC_ANCHOR_SEPARATOR.join(item.text for item in items)


def resolve_global_span(
    segments: Iterable[SemanticTextSegment],
    reference: SemanticTextRef,
    *,
    text_span: str,
    expected_slot_id: int,
) -> tuple[int, int]:
    """Validate one segment-local span and return a global code-point range."""

    items = tuple(segments)
    anchor = build_semantic_anchor(items)
    by_id = {item.segment_id: item for item in items}
    segment = by_id.get(reference.segment_id)
    if segment is None:
        raise ValueError(f"unknown segment: {reference.segment_id}")
    if (
        segment.locked_slot_id is not None
        and segment.locked_slot_id != expected_slot_id
    ):
        raise ValueError(
            f"locked slot mismatch: expected {segment.locked_slot_id}, got {expected_slot_id}"
        )
    if reference.local_end > len(segment.text):
        raise ValueError("local span is out of bounds")
    if segment.text[reference.local_start : reference.local_end] != text_span:
        raise ValueError("local span mismatch")

    global_start = segment.anchor_start + reference.local_start
    global_end = segment.anchor_start + reference.local_end
    if anchor[global_start:global_end] != text_span:
        raise ValueError("global span mismatch")
    if global_end - global_start != len(text_span):
        raise ValueError("global span length mismatch")
    return global_start, global_end
