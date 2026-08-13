from datetime import UTC, datetime

from app.assemblers.fallbacks import (
    assemble_unknown_claim_evidence_fallback,
    assemble_unverifiable_evaluation_fallback,
    omit_invalid_findings_fallback,
)
from app.schemas.frozen import NumericCheck, ReasonCode

NOW = datetime(2026, 8, 14, tzinfo=UTC)
IDS = ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FAW"]


def test_n7_fallback_discards_draft_and_marks_all_packet_items_rule_unknown():
    items = assemble_unknown_claim_evidence_fallback("01ARZ3NDEKTSV4RRFFQ69G5FAX", IDS, {IDS[0]: "01ARZ3NDEKTSV4RRFFQ69G5FAY", IDS[1]: "01ARZ3NDEKTSV4RRFFQ69G5FAZ"})
    assert [item.evidence_id for item in items] == IDS
    assert {(item.stance, item.stance_source, item.confidence) for item in items} == {("unknown", "rule", None)}


def test_n8_fallback_is_system_owned_unverifiable_and_preserves_numeric_checks():
    check = NumericCheck(metric="매출", claimed="10", result="no_data", evidence_id=IDS[0])
    item = assemble_unverifiable_evaluation_fallback(
        claim_id="01ARZ3NDEKTSV4RRFFQ69G5FAX", packet_evidence_ids=IDS,
        numeric_checks=[check], claim_evaluation_id="01ARZ3NDEKTSV4RRFFQ69G5FAY", created_at=NOW,
    )
    assert item.verdict == "unverifiable"
    assert item.unknown_evidence_ids == IDS
    assert item.numeric_checks == [check]
    assert item.uncertainty_codes == [ReasonCode.COVERAGE_TRUNCATED]
    assert not item.citations and not item.support_evidence_ids and not item.oppose_evidence_ids


def test_n9_fallback_salvages_no_invalid_llm_finding():
    assert omit_invalid_findings_fallback() == []
