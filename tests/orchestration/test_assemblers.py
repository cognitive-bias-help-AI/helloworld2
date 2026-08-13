from datetime import UTC, datetime

import pytest

from app.assemblers.claim_evaluation import assemble_claim_evaluation
from app.assemblers.claim_evidence import assemble_claim_evidence
from app.assemblers.errors import AssemblyError
from app.assemblers.findings import assemble_findings
from app.orchestration.drafts import FindingDraft
from app.schemas.frozen import (
    CitationRef,
    ClaimEvaluation,
    ClaimEvaluationDraft,
    ClaimEvidenceDraft,
    ClaimStanceDraft,
    NumericCheck,
    ReasonCode,
)

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def U(n):
    return f"01K5ZTQ9X7WPCVN2M4H8JRAB{n}D"


EVALUATION_ID = U(7)
CLAIM_ID = U(8)
EVIDENCE_ID = U(1)


def assert_error(call, kind, reason, retryable):
    with pytest.raises(AssemblyError) as caught:
        call()
    assert (caught.value.kind, caught.value.reason_code, caught.value.retryable) == (
        kind,
        reason,
        retryable,
    )


def evaluation(eid=EVALUATION_ID, claim=CLAIM_ID, evidence=EVIDENCE_ID):
    return ClaimEvaluation(
        claim_evaluation_id=eid,
        claim_id=claim,
        citations=[],
        support_evidence_ids=[],
        oppose_evidence_ids=[],
        neutral_evidence_ids=[],
        unknown_evidence_ids=[evidence],
        numeric_checks=[],
        verdict="unverifiable",
        missing_dimensions=[],
        uncertainty_codes=[],
        created_at=NOW,
    )


def test_AssemblyError_kind_reason_mapping과_retryability를_보존한다():
    expected = {
        "coverage_mismatch": ReasonCode.COVERAGE_TRUNCATED,
        "unknown_reference": ReasonCode.CONTRACT_VIOLATION,
        "duplicate_reference": ReasonCode.CONTRACT_VIOLATION,
        "contract_violation": ReasonCode.CONTRACT_VIOLATION,
    }
    for kind, reason in expected.items():
        error = AssemblyError(kind, retryable=kind != "contract_violation")
        assert error.reason_code is reason


def test_n7은_exact_coverage와_query_lineage를_ID순으로_조립한다():
    draft = ClaimStanceDraft(
        stances=[
            ClaimEvidenceDraft(evidence_id=U(2), stance="oppose"),
            ClaimEvidenceDraft(evidence_id=U(1), stance="support", confidence=0.8),
        ]
    )
    result = assemble_claim_evidence(draft, U(8), [U(2), U(1)], {U(1): U(3), U(2): U(4)})
    assert [item.evidence_id for item in result] == [U(1), U(2)]
    assert [item.query_id for item in result] == [U(3), U(4)]
    assert all(item.claim_id == U(8) and item.stance_source == "llm" for item in result)


@pytest.mark.parametrize(
    ("draft_ids", "packet_ids", "kind"),
    [([1], [1, 2], "coverage_mismatch"), ([1, 3], [1, 2], "unknown_reference")],
)
def test_n7은_누락과_unknown을_재시도가능_오류로_구분한다(draft_ids, packet_ids, kind):
    draft = ClaimStanceDraft(
        stances=[ClaimEvidenceDraft(evidence_id=U(n), stance="unknown") for n in draft_ids]
    )
    assert_error(
        lambda: assemble_claim_evidence(
            draft, U(8), [U(n) for n in packet_ids], {U(n): U(n + 4) for n in packet_ids}
        ),
        kind,
        ReasonCode.COVERAGE_TRUNCATED if kind == "coverage_mismatch" else ReasonCode.CONTRACT_VIOLATION,
        True,
    )


def test_n7은_packet과_mapping의_caller_duplicate_mismatch를_거부한다():
    draft = ClaimStanceDraft(stances=[ClaimEvidenceDraft(evidence_id=U(1), stance="unknown")])
    assert_error(
        lambda: assemble_claim_evidence(draft, U(8), [U(1), U(1)], {U(1): U(3)}),
        "duplicate_reference", ReasonCode.CONTRACT_VIOLATION, False,
    )
    assert_error(
        lambda: assemble_claim_evidence(draft, U(8), [U(1)], {}),
        "contract_violation", ReasonCode.CONTRACT_VIOLATION, False,
    )


def base_eval_draft(**changes):
    data = dict(
        citations=[CitationRef(evidence_id=U(1), span="근거")],
        support_evidence_ids=[U(1)], oppose_evidence_ids=[], neutral_evidence_ids=[],
        unknown_evidence_ids=[U(2)], verdict="support", missing_dimensions=[], uncertainty_codes=[],
    )
    data.update(changes)
    return ClaimEvaluationDraft(**data)


def test_n8은_rule_numeric과_ID_time을_주입하고_정렬한다():
    check = NumericCheck(metric="매출", claimed="1", result="no_data", evidence_id=U(2))
    result = assemble_claim_evaluation(base_eval_draft(), U(8), [U(2), U(1)], [check], U(7), NOW)
    assert result.claim_evaluation_id == U(7) and result.created_at == NOW
    assert result.numeric_checks == [check]
    assert result.support_evidence_ids == [U(1)]


def test_n8은_inconsistent_NumericCheck로_LLM_verdict를_바꾸지_않는다():
    check = NumericCheck(
        metric="매출", claimed="1", observed=2, result="inconsistent", evidence_id=U(2)
    )
    result = assemble_claim_evaluation(base_eval_draft(), U(8), [U(1), U(2)], [check], U(7), NOW)
    assert result.verdict == "support"


def test_n8은_packet_duplicate_unknown_missing_numeric_unknown을_구분한다():
    assert_error(lambda: assemble_claim_evaluation(base_eval_draft(), U(8), [U(1), U(1)], [], U(7), NOW), "duplicate_reference", ReasonCode.CONTRACT_VIOLATION, False)
    unknown = base_eval_draft(unknown_evidence_ids=[U(3)])
    assert_error(lambda: assemble_claim_evaluation(unknown, U(8), [U(1), U(2)], [], U(7), NOW), "unknown_reference", ReasonCode.CONTRACT_VIOLATION, True)
    missing = base_eval_draft(unknown_evidence_ids=[])
    assert_error(lambda: assemble_claim_evaluation(missing, U(8), [U(1), U(2)], [], U(7), NOW), "coverage_mismatch", ReasonCode.COVERAGE_TRUNCATED, True)
    check = NumericCheck(metric="x", claimed="1", result="no_data", evidence_id=U(3))
    assert_error(lambda: assemble_claim_evaluation(base_eval_draft(), U(8), [U(1), U(2)], [check], U(7), NOW), "unknown_reference", ReasonCode.CONTRACT_VIOLATION, False)


def test_n8은_bypass된_bucket_overlap과_citation_unknown을_잡는다():
    overlap = ClaimEvaluationDraft.model_construct(**(base_eval_draft().model_dump() | {"oppose_evidence_ids": [U(1)]}))
    assert_error(lambda: assemble_claim_evaluation(overlap, U(8), [U(1), U(2)], [], U(7), NOW), "duplicate_reference", ReasonCode.CONTRACT_VIOLATION, True)
    bad_citation = ClaimEvaluationDraft.model_construct(**(base_eval_draft().model_dump() | {"citations": [CitationRef(evidence_id=U(3), span="x")]}))
    assert_error(lambda: assemble_claim_evaluation(bad_citation, U(8), [U(1), U(2)], [], U(7), NOW), "unknown_reference", ReasonCode.CONTRACT_VIOLATION, True)


def test_n9은_semantic_sort후_ID를_주입하고_missing_None을_허용한다():
    ev = evaluation()
    drafts = [FindingDraft(slot_id=2, kind="missing", citations=[]), FindingDraft(slot_id=1, kind="unverified", citations=[CitationRef(evidence_id=U(1), span="x")], claim_evaluation_id=ev.claim_evaluation_id)]
    result = assemble_findings(drafts, [ev], [U(4), U(5)], NOW)
    assert [(x.slot_id, x.finding_id) for x in result] == [(1, U(4)), (2, U(5))]


def test_n9은_unknown_eval_evidence와_mismatch_no_citation을_구분한다():
    ev = evaluation()
    unknown_eval = FindingDraft(slot_id=1, kind="unverified", citations=[], claim_evaluation_id=U(6))
    assert_error(lambda: assemble_findings([unknown_eval], [ev], [U(4)], NOW), "unknown_reference", ReasonCode.CONTRACT_VIOLATION, True)
    unknown_evidence = FindingDraft(slot_id=1, kind="unverified", citations=[CitationRef(evidence_id=U(2), span="x")], claim_evaluation_id=ev.claim_evaluation_id)
    assert_error(lambda: assemble_findings([unknown_evidence], [ev], [U(4)], NOW), "unknown_reference", ReasonCode.CONTRACT_VIOLATION, True)
    mismatch = FindingDraft(slot_id=1, kind="mismatch", citations=[], claim_evaluation_id=ev.claim_evaluation_id)
    assert_error(lambda: assemble_findings([mismatch], [ev], [U(4)], NOW), "contract_violation", ReasonCode.CONTRACT_VIOLATION, True)


def test_n9은_semantic_duplicate와_caller_ID_contract를_거부한다():
    draft = FindingDraft(slot_id=1, kind="missing", citations=[])
    assert_error(lambda: assemble_findings([draft, draft], [], [U(4), U(5)], NOW), "duplicate_reference", ReasonCode.CONTRACT_VIOLATION, True)
    assert_error(lambda: assemble_findings([draft], [], [], NOW), "contract_violation", ReasonCode.CONTRACT_VIOLATION, False)
    assert_error(lambda: assemble_findings([draft, FindingDraft(slot_id=2, kind="missing", citations=[])], [], [U(4), U(4)], NOW), "duplicate_reference", ReasonCode.CONTRACT_VIOLATION, False)
