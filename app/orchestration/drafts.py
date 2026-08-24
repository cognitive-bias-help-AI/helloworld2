"""Closed LLM output contracts owned by orchestration nodes."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator, model_validator

from app.domain.evidence_requirement import EvidenceCategory
from app.domain.semantic import SemanticKind
from app.schemas.frozen import ULID, CitationRef, NonBlankStr, ReasonCode, SlotId, Violation


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


SpanOffsetDraft = Annotated[
    tuple[int, int],
    WithJsonSchema(
        {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 2,
            "maxItems": 2,
        }
    ),
]


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
    span_offset: SpanOffsetDraft
    normalized_proposition: NonBlankStr
    verifiable: bool


class SlotExtractionDraft(OutputModel):
    claims: list[ExtractedClaimDraft]


class SemanticUnitDraft(OutputModel):
    """Ephemeral semantic proposal; it is not a canonical Claim."""

    segment_id: NonBlankStr
    slot_id: SlotId
    text_span: NonBlankStr
    span_offset: SpanOffsetDraft
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


class EvidenceRequirementDraft(OutputModel):
    category: EvidenceCategory
    topic_terms: list[NonBlankStr] = Field(default_factory=list, max_length=5)
    direction: NonBlankStr | None = None
    actor: NonBlankStr | None = None
    comparison_target: NonBlankStr | None = None
    temporal_expression: NonBlankStr | None = None


class EvidenceIntentDraft(OutputModel):
    requirements: list[EvidenceRequirementDraft] = Field(max_length=3)

    @model_validator(mode="after")
    def reject_duplicate_categories(self):
        categories = [item.category for item in self.requirements]
        if len(categories) != len(set(categories)):
            raise ValueError("evidence categories must be unique per claim")
        return self


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


class ViolationDraft(OutputModel):
    slot_no: SlotId
    rule_id: NonBlankStr
    kind: Literal["lexicon", "pattern", "structure"]
    matched: NonBlankStr
    span_offset: SpanOffsetDraft

    @field_validator("span_offset")
    @classmethod
    def validate_span_offset(cls, value: tuple[int, int]) -> tuple[int, int]:
        start, end = value
        if start < 0 or end <= start:
            raise ValueError("span_offset requires 0 <= start < end")
        return value

    def to_canonical(self) -> Violation:
        return Violation.model_validate(self.model_dump())


class GuardVerdictDraft(OutputModel):
    violations: list[ViolationDraft]


class RenderedSlotDraft(OutputModel):
    slot_no: SlotId
    text: NonBlankStr
    citations: list[CitationRef]


class RenderDraft(OutputModel):
    slots: list[RenderedSlotDraft]
