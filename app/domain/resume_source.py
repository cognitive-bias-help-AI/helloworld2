"""Append-only HITL resume semantic-source contract."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.semantic_source import (
    SEMANTIC_PROJECTION_VERSION,
    SemanticProjectionVersion,
    SemanticTextRef,
    SemanticTextSegment,
)
from app.domain.text_safety import sanitize_user_text
from app.schemas.frozen import ULID, NonBlankStr, SlotId, SourceTrace

RESUME_SOURCE_SCHEMA_VERSION = "resume_semantic_source/v1"


class _ResumeSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class ResumeSemanticSource(_ResumeSourceModel):
    """Sanitized canonical source for one targeted HITL resume event."""

    schema_version: Literal["resume_semantic_source/v1"] = RESUME_SOURCE_SCHEMA_VERSION
    source_id: ULID
    resume_key: NonBlankStr
    slot_id: SlotId
    issue_id: NonBlankStr | None = None
    origin: Literal[SourceTrace.USER_CONFIRMED] = SourceTrace.USER_CONFIRMED
    sanitized_text: NonBlankStr
    segment_id: NonBlankStr
    semantic_projection_version: SemanticProjectionVersion = SEMANTIC_PROJECTION_VERSION

    @model_validator(mode="after")
    def enforce_canonical_body(self):
        if self.segment_id != f"resume:{self.source_id}":
            raise ValueError("resume segment_id must derive from source_id")
        if sanitize_user_text(self.sanitized_text) != self.sanitized_text:
            raise ValueError("resume source text must already be sanitized")
        return self


def expected_resume_source_id(run_id: str, resume_key: str) -> str:
    """Bind event identity to its run while leaving payload conflict-detectable."""

    if not run_id.strip():
        raise ValueError("run_id must be non-blank")
    if not resume_key.strip():
        raise ValueError("resume_key must be non-blank")
    seed = f"resume_source|{run_id}|{resume_key}"
    return "01" + sha256(seed.encode("utf-8")).hexdigest().upper()[:24]


def build_resume_semantic_source(
    run_id: str,
    *,
    resume_key: str,
    slot_id: int,
    raw_text: str,
    issue_id: str | None = None,
) -> ResumeSemanticSource:
    """Sanitize one transport answer and mint its deterministic source identity."""

    source_id = expected_resume_source_id(run_id, resume_key)
    return ResumeSemanticSource(
        source_id=source_id,
        resume_key=resume_key,
        slot_id=slot_id,
        issue_id=issue_id,
        sanitized_text=sanitize_user_text(raw_text),
        segment_id=f"resume:{source_id}",
    )


def build_resume_segment(
    source: ResumeSemanticSource, *, anchor_start: int
) -> SemanticTextSegment:
    """Hydrate a resume source for composition after initial semantic segments."""

    return SemanticTextSegment(
        segment_id=source.segment_id,
        origin=SourceTrace.USER_CONFIRMED,
        locked_slot_id=source.slot_id,
        text=source.sanitized_text,
        anchor_start=anchor_start,
        anchor_end=anchor_start + len(source.sanitized_text),
    )


def build_resume_text_ref(source: ResumeSemanticSource) -> SemanticTextRef:
    """Reference the complete sanitized resume segment without copying its text."""

    return SemanticTextRef(
        segment_id=source.segment_id,
        local_start=0,
        local_end=len(source.sanitized_text),
    )
