import pytest
from pydantic import ValidationError

from app.domain.semantic import SemanticKind
from app.orchestration.drafts import (
    AskBackDraft,
    AskBackQuestionDraft,
    ExtractedClaimDraft,
    FindingDraft,
    GuardScanResult,
    GuardVerdictDraft,
    RenderDraft,
    RenderedSlotDraft,
    SemanticExtractionDraft,
    SemanticUnitDraft,
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


def test_SemanticUnitDraft는_Claim이_아닌_최소_proposal_fields만_갖는다():
    draft = SemanticUnitDraft(
        segment_id="structured:S4",
        slot_id=4,
        text_span="HBM 공급이 부족하다",
        span_offset=(0, 12),
        normalized_proposition="HBM 공급이 부족하다",
        proposed_value=None,
        semantic_kind=SemanticKind.EXTERNAL_ASSERTION,
    )

    assert set(SemanticUnitDraft.model_fields) == {
        "segment_id",
        "slot_id",
        "text_span",
        "span_offset",
        "normalized_proposition",
        "proposed_value",
        "semantic_kind",
    }
    assert draft.span_offset == (0, 12)
    assert not {
        "verifiable",
        "source",
        "origin",
        "claim_id",
        "created_at",
        "conflict_status",
    } & set(SemanticUnitDraft.model_fields)


@pytest.mark.parametrize(
    "forbidden,value",
    [
        ("verifiable", True),
        ("source", "survey"),
        ("origin", "llm_extraction"),
        ("claim_id", U1),
        ("created_at", "2026-08-18T00:00:00Z"),
        ("conflict_status", "conflict"),
    ],
)
def test_SemanticUnitDraft는_canonical_authority_fields를_거부한다(forbidden, value):
    fields = {
        "segment_id": "free_text:0",
        "slot_id": 2,
        "text_span": "아직 보유하지 않았다",
        "span_offset": (0, 11),
        "normalized_proposition": None,
        "proposed_value": "NOT_HOLDING",
        "semantic_kind": SemanticKind.USER_STATE,
        forbidden: value,
    }

    with pytest.raises(ValidationError):
        SemanticUnitDraft(**fields)


def test_external_kind는_normalized_proposition을_요구하고_context는_None을_허용한다():
    with pytest.raises(ValidationError, match="external semantic kind requires"):
        SemanticUnitDraft(
            segment_id="structured:S4",
            slot_id=4,
            text_span="HBM 공급 부족",
            span_offset=(0, 8),
            normalized_proposition=None,
            proposed_value=None,
            semantic_kind=SemanticKind.EXTERNAL_ASSERTION,
        )

    context = SemanticUnitDraft(
        segment_id="free_text:0",
        slot_id=3,
        text_span="장기로 보고 있다",
        span_offset=(0, 9),
        normalized_proposition=None,
        proposed_value="LONG",
        semantic_kind=SemanticKind.USER_PREFERENCE,
    )
    assert context.normalized_proposition is None


@pytest.mark.parametrize("span_offset", [(-1, 2), (2, 2), (3, 2)])
def test_SemanticUnitDraft의_local_span_offset은_정방향이어야_한다(span_offset):
    with pytest.raises(ValidationError):
        SemanticUnitDraft(
            segment_id="free_text:0",
            slot_id=2,
            text_span="보유 중",
            span_offset=span_offset,
            normalized_proposition=None,
            proposed_value="HOLDING",
            semantic_kind=SemanticKind.USER_STATE,
        )


def test_SemanticExtractionDraft는_typed_units를_JSON_safe하게_감싼다():
    unit = SemanticUnitDraft(
        segment_id="free_text:0",
        slot_id=6,
        text_span="뉴스를 확인했다",
        span_offset=(0, 8),
        normalized_proposition=None,
        proposed_value=("NEWS",),
        semantic_kind=SemanticKind.INFORMATION_CHECKED,
    )

    draft = SemanticExtractionDraft(units=[unit])

    assert draft.units == [unit]
    assert draft.model_dump(mode="json")["units"][0]["proposed_value"] == ["NEWS"]
    with pytest.raises(ValidationError):
        SemanticExtractionDraft(units=[unit], verifiable=True)


def test_SemanticExtractionDraft_span_offset_schema는_MLAPI_array_items_형식이다():
    schema = SemanticExtractionDraft.model_json_schema()
    span_schema = schema["$defs"]["SemanticUnitDraft"]["properties"]["span_offset"]

    assert span_schema["type"] == "array"
    assert span_schema["items"] == {"type": "integer"}
    assert span_schema["minItems"] == 2
    assert span_schema["maxItems"] == 2
    assert "prefixItems" not in span_schema


@pytest.mark.parametrize("span_offset", [[-1, 5], [5, 5], [5, 4], [1], [1, 2, 3]])
def test_SemanticUnitDraft_wire_span_offset도_정확한_정방향_두_정수다(span_offset):
    with pytest.raises(ValidationError):
        SemanticUnitDraft(
            segment_id="free_text:0",
            slot_id=2,
            text_span="보유 중",
            span_offset=span_offset,
            normalized_proposition=None,
            proposed_value="HOLDING",
            semantic_kind=SemanticKind.USER_STATE,
        )

    valid = SemanticUnitDraft(
        segment_id="free_text:0",
        slot_id=2,
        text_span="보유 중",
        span_offset=[0, 5],
        normalized_proposition=None,
        proposed_value="HOLDING",
        semantic_kind=SemanticKind.USER_STATE,
    )
    assert valid.span_offset == (0, 5)
