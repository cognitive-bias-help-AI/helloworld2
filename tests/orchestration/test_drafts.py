import pytest
from pydantic import ValidationError

from app.orchestration.drafts import (
    AskBackDraft,
    AskBackQuestionDraft,
    ExtractedClaimDraft,
    FindingDraft,
    GuardScanResult,
    GuardVerdictDraft,
    RenderDraft,
    RenderedSlotDraft,
    SlotExtractionDraft,
)
from app.schemas.frozen import CitationRef, ReasonCode, Violation

U1 = "01K5ZTQ9X7WPCVN2M4H8JRAB1D"


def test_output_schema_6종은_최소_필드로_생성된다():
    claim = ExtractedClaimDraft(
        slot_id=1,
        user_text_span="실적이 좋아졌다",
        span_offset=(0, 8),
        normalized_proposition="실적이 개선됐다",
        verifiable=True,
    )
    question = AskBackQuestionDraft(slot_id=1, question="어떤 기간을 말하나요?")
    citation = CitationRef(evidence_id=U1, span="근거")
    violation = Violation(
        slot_no=1, rule_id="R1", kind="pattern", matched="사세요", span_offset=(0, 3)
    )
    rendered = RenderedSlotDraft(slot_no=1, text="검토 결과", citations=[citation])

    assert GuardScanResult().reason_code is None
    assert SlotExtractionDraft(claims=[claim]).claims == [claim]
    assert AskBackDraft(questions=[question]).questions == [question]
    assert FindingDraft(slot_id=1, kind="missing", citations=[]).claim_evaluation_id is None
    assert GuardVerdictDraft(violations=[violation]).violations == [violation]
    assert RenderDraft(slots=[rendered]).slots == [rendered]


def test_output_schema는_extra와_mutation과_system_field를_거부한다():
    with pytest.raises(ValidationError):
        GuardScanResult(extra_field=True)
    result = GuardScanResult()
    with pytest.raises(ValidationError):
        result.reason_code = ReasonCode.OUT_OF_SCOPE
    with pytest.raises(ValidationError):
        FindingDraft(slot_id=1, kind="missing", citations=[], finding_id=U1)


@pytest.mark.parametrize("kind", ["mismatch", "unverified", "conflict"])
def test_non_missing_Finding은_evaluation_lineage가_필수다(kind):
    with pytest.raises(ValidationError):
        FindingDraft(slot_id=1, kind=kind, citations=[], claim_evaluation_id=None)


def test_GuardScanResult는_승인된_6개_reason만_허용한다():
    assert GuardScanResult(reason_code=ReasonCode.PROMPT_INJECTION).reason_code
    with pytest.raises(ValidationError):
        GuardScanResult(reason_code=ReasonCode.RATE_LIMIT)
