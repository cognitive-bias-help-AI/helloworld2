"""P0-3 노드별 최소권한 View와 transport envelope."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.frozen import (
    ULID,
    CitationRef,
    ClaimEvaluation,
    GuardInput,
    HttpUrlStr,
    NonBlankStr,
    NumericCheck,
    OpposeBlock,
    SlotId,
    TheoryNote,
)


class _ViewModel(BaseModel):
    """새 View의 공통 폐쇄·불변 계약."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class SlotDefinitionView(_ViewModel):
    slot_id: SlotId
    name: NonBlankStr
    description: NonBlankStr


class MissingSlotView(_ViewModel):
    slot_id: SlotId
    status: Literal["absent", "partial", "conflict"]
    summary: NonBlankStr


class ClaimView(_ViewModel):
    claim_id: ULID
    slot_id: SlotId
    normalized_proposition: NonBlankStr


class EvidenceExcerptView(_ViewModel):
    evidence_id: ULID
    source_type: Literal["dart", "news", "quote"]
    source_ref: NonBlankStr
    publisher: NonBlankStr | None = None
    published_at: AwareDatetime | None = None
    as_of: AwareDatetime
    raw_span: NonBlankStr = Field(max_length=500)


class ClassifiedEvidenceView(EvidenceExcerptView):
    stance: Literal["support", "oppose", "neutral", "unknown"]


class SlotTextView(_ViewModel):
    slot_no: SlotId
    text: NonBlankStr
    quoted: bool
    citations: list[CitationRef]


class RenderCitationView(_ViewModel):
    evidence_id: ULID
    span: NonBlankStr = Field(max_length=500)
    source_url: HttpUrlStr | None = None
    publisher: NonBlankStr | None = None


class GuardScanView(_ViewModel):
    masked_input: NonBlankStr


class SlotContext(_ViewModel):
    masked_input: NonBlankStr
    slot_definitions: list[SlotDefinitionView]


class AskBackContext(_ViewModel):
    missing_slots: list[MissingSlotView]


class EvidencePacket(_ViewModel):
    claim: ClaimView
    evidence: list[EvidenceExcerptView]


class VerifyPacket(_ViewModel):
    claim: ClaimView
    evidence: list[ClassifiedEvidenceView]
    numeric_checks: list[NumericCheck]


class IntegrationView(_ViewModel):
    evaluations: list[ClaimEvaluation]
    oppose: OpposeBlock
    missing_slots: list[MissingSlotView]


class GuardBatchEnvelope(_ViewModel):
    """GuardInput 여러 건을 ModelGateway로 운반하는 비-semantic 컨테이너."""

    items: list[GuardInput]


class RenderView(_ViewModel):
    slots: list[SlotTextView]
    banners: list[NonBlankStr]
    theory_notes: list[TheoryNote]
    citations: list[RenderCitationView]
