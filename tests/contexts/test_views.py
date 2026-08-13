"""P0-3 LLM View 최소권한 계약 회귀."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.contexts.views import (
    AskBackContext,
    ClaimView,
    ClassifiedEvidenceView,
    EvidenceExcerptView,
    EvidencePacket,
    GuardBatchEnvelope,
    GuardScanView,
    IntegrationView,
    MissingSlotView,
    RenderCitationView,
    RenderView,
    SlotContext,
    SlotDefinitionView,
    SlotTextView,
    VerifyPacket,
)
from app.schemas.frozen import (
    ClaimEvaluation,
    GuardInput,
    NumericCheck,
    OpposeBlock,
    TheoryNote,
)

E1 = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
C1 = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
CE1 = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
NOW = datetime(2026, 8, 13, tzinfo=UTC)


def claim_view() -> ClaimView:
    return ClaimView(claim_id=C1, slot_id=1, normalized_proposition="매출이 증가한다")


def evidence_view(**overrides) -> EvidenceExcerptView:
    values = {
        "evidence_id": E1,
        "source_type": "news",
        "source_ref": "article-1",
        "publisher": "Example News",
        "published_at": NOW,
        "as_of": NOW,
        "raw_span": "매출이 전년 대비 증가했다.",
    }
    values.update(overrides)
    return EvidenceExcerptView(**values)


def test_새_View는_extra를_거부하고_생성_후_필드_교체를_막는다():
    with pytest.raises(ValidationError):
        GuardScanView(masked_input="검토할 판단", claims=[])

    view = GuardScanView(masked_input="검토할 판단")
    with pytest.raises(ValidationError):
        view.masked_input = "변경"


def test_ClaimView는_승인된_세_필드만_갖는다():
    assert set(ClaimView.model_fields) == {
        "claim_id",
        "slot_id",
        "normalized_proposition",
    }
    assert not {
        "user_text_span",
        "span_offset",
        "origin",
        "superseded_by",
        "created_at",
    } & set(ClaimView.model_fields)


def test_EvidenceExcerptView는_획득_저장소_내부_필드가_없다():
    assert set(EvidenceExcerptView.model_fields) == {
        "evidence_id",
        "source_type",
        "source_ref",
        "publisher",
        "published_at",
        "as_of",
        "raw_span",
    }
    assert not {"content_sha256", "provider_request_id", "fetched_at"} & set(
        EvidenceExcerptView.model_fields
    )
    with pytest.raises(ValidationError):
        evidence_view(content_sha256="a" * 64)


def test_VerifyPacket은_n7의_oppose_stance를_같은_Evidence_ID와_함께_보존한다():
    classified = ClassifiedEvidenceView(**evidence_view().model_dump(), stance="oppose")
    packet = VerifyPacket(claim=claim_view(), evidence=[classified], numeric_checks=[])

    assert packet.evidence[0].evidence_id == E1
    assert packet.evidence[0].stance == "oppose"


def test_8개_semantic_View의_허용_필드가_고정된다():
    expected = {
        GuardScanView: {"masked_input"},
        SlotContext: {"masked_input", "slot_definitions"},
        AskBackContext: {"missing_slots"},
        EvidencePacket: {"claim", "evidence"},
        VerifyPacket: {"claim", "evidence", "numeric_checks"},
        IntegrationView: {"evaluations", "oppose", "missing_slots"},
        GuardInput: {"slot_no", "text", "quoted", "citations"},
        RenderView: {"slots", "banners", "theory_notes", "citations", "guard_feedback"},
    }

    assert {model: set(model.model_fields) for model in expected} == expected


def test_semantic_View를_실제_frozen_계약으로_구성할_수_있다():
    slot_definition = SlotDefinitionView(slot_id=1, name="투자 목표", description="판단의 목표")
    missing = MissingSlotView(slot_id=2, status="partial", summary="기간이 불명확함")
    evaluation = ClaimEvaluation(
        claim_evaluation_id=CE1,
        claim_id=C1,
        citations=[],
        support_evidence_ids=[],
        oppose_evidence_ids=[],
        unknown_evidence_ids=[],
        numeric_checks=[],
        verdict="unsupported",
        missing_dimensions=[2],
        uncertainty_codes=[],
        created_at=NOW,
    )
    oppose = OpposeBlock(status="verified", count=0, queries=["반대 근거 검색"])
    slot = SlotTextView(slot_no=1, text="검토 결과", quoted=False, citations=[])
    citation = RenderCitationView(
        evidence_id=E1,
        span="인용 원문",
        source_url="https://example.com/a",
        publisher="Example News",
    )
    theory = TheoryNote(
        theory_id="loss-aversion",
        trigger=(2, "partial"),
        name="손실회피",
        definition="손실을 더 크게 느끼는 경향",
        observable_pattern="손실 가능성만 과도하게 강조함",
        non_diagnostic_warning="진단이 아니라 관찰 가능한 패턴 설명입니다.",
        source_refs=["reference-1"],
    )

    assert SlotContext(masked_input="판단", slot_definitions=[slot_definition])
    assert AskBackContext(missing_slots=[missing])
    assert EvidencePacket(claim=claim_view(), evidence=[evidence_view()])
    assert IntegrationView(evaluations=[evaluation], oppose=oppose, missing_slots=[missing])
    assert RenderView(
        slots=[slot], banners=["근거 부족"], theory_notes=[theory], citations=[citation]
    )


def test_GuardBatchEnvelope은_GuardInput만_담고_extra를_거부한다():
    item = GuardInput(slot_no=1, text="검토 결과", quoted=False, citations=[])
    envelope = GuardBatchEnvelope(items=[item])

    assert envelope.items == [item]
    assert set(GuardBatchEnvelope.model_fields) == {"items"}
    with pytest.raises(ValidationError):
        GuardBatchEnvelope(items=[item], findings=[])


def test_VerifyPacket은_NumericCheck를_입력으로_보존한다():
    check = NumericCheck(
        metric="영업이익",
        claimed="증가",
        observed=None,
        result="no_data",
        evidence_id=E1,
    )
    packet = VerifyPacket(claim=claim_view(), evidence=[], numeric_checks=[check])

    assert packet.numeric_checks == [check]
