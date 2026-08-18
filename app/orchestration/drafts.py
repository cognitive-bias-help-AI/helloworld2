"""Closed LLM output contracts owned by orchestration nodes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.semantic import SemanticKind
from app.schemas.frozen import ULID, CitationRef, NonBlankStr, ReasonCode, SlotId, Violation


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class GuardScanResult(OutputModel):
    reason_code: Literal[
        ReasonCode.SELF_HARM_SIGNAL,
        ReasonCode.ILLEGAL_REQUEST,
        ReasonCode.PII_DETECTED,
        ReasonCode.OUT_OF_SCOPE,
        ReasonCode.PROMPT_INJECTION,
        ReasonCode.INPUT_INSUFFICIENT,
    ] | None = None


class ExtractedClaimDraft(OutputModel):
    slot_id: SlotId
    user_text_span: NonBlankStr
    span_offset: tuple[int, int]
    normalized_proposition: NonBlankStr
    verifiable: bool


class SlotExtractionDraft(OutputModel):
    claims: list[ExtractedClaimDraft]


class SemanticUnitDraft(OutputModel):
    """Ephemeral semantic proposal; it is not a canonical Claim."""

    segment_id: NonBlankStr
    slot_id: SlotId
    text_span: NonBlankStr
    span_offset: tuple[int, int]
    normalized_proposition: NonBlankStr | None
    proposed_value: str | tuple[str, ...] | None
    semantic_kind: SemanticKind

    @field_validator("span_offset")
    @classmethod
    def validate_local_span_offset(cls, value: tuple[int, int]) -> tuple[int, int]:
        start, end = value
        if start < 0 or end <= start:
            raise ValueError("local span_offset requires 0 <= start < end")
        return value

    @model_validator(mode="after")
    def require_external_proposition(self):
        if self.semantic_kind in {
            SemanticKind.EXTERNAL_ASSERTION,
            SemanticKind.EXTERNAL_EXPECTATION,
        } and self.normalized_proposition is None:
            raise ValueError("external semantic kind requires normalized_proposition")
        return self


class SemanticExtractionDraft(OutputModel):
    units: list[SemanticUnitDraft]


class AskBackQuestionDraft(OutputModel):
    slot_id: SlotId
    question: NonBlankStr


class AskBackDraft(OutputModel):
    questions: list[AskBackQuestionDraft]


class FindingDraft(OutputModel):
    slot_id: SlotId
    kind: Literal["mismatch", "missing", "unverified", "conflict"]
    citations: list[CitationRef]
    claim_evaluation_id: ULID | None = None

    @model_validator(mode="after")
    def enforce_evaluation_lineage(self):
        if self.kind != "missing" and self.claim_evaluation_id is None:
            raise ValueError("missing 이외 FindingDraft는 claim_evaluation_id 필수")
        return self


class GuardVerdictDraft(OutputModel):
    violations: list[Violation]


class RenderedSlotDraft(OutputModel):
    slot_no: SlotId
    text: NonBlankStr
    citations: list[CitationRef]


class RenderDraft(OutputModel):
    slots: list[RenderedSlotDraft]
