import asyncio
from datetime import UTC, datetime

from app.schemas.frozen import Claim, ClaimEvaluation, ClaimEvidence, Finding, SourceTrace
from app.store.memory_review_store import MemoryReviewStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)
run = asyncio.run


def U(n):
    return f"01K5ZTQ9X7WPCVN2M4H8JRAB{n}D"


def claim(n=1):
    return Claim(
        claim_id=U(n),
        slot_id=3,
        user_text_span="text",
        span_offset=(0, 4),
        normalized_proposition="prop",
        verifiable=True,
        origin=SourceTrace.LLM_EXTRACTION,
        created_at=NOW,
    )


def evaluation(n, claim_id):
    return ClaimEvaluation(
        claim_evaluation_id=U(n),
        claim_id=claim_id,
        citations=[],
        support_evidence_ids=[],
        oppose_evidence_ids=[],
        unknown_evidence_ids=[],
        numeric_checks=[],
        verdict="unverifiable",
        missing_dimensions=[],
        uncertainty_codes=[],
        created_at=NOW,
    )


def test_ReviewStore_12개_method와_본문_claim_finding_report를_왕복한다():
    store = MemoryReviewStore()
    c = claim()
    f = Finding(finding_id=U(4), slot_id=3, kind="missing", citations=[], created_at=NOW)
    input_id = run(store.put_input("r", {"a": 1}))
    report_id = run(store.put_report("r", {"b": 2}))
    assert run(store.get_input(input_id)) == {"a": 1}
    assert run(store.get_report(report_id)) == {"b": 2}
    assert run(store.put_claims("r", [c])) == [c.claim_id]
    assert run(store.get_claims([c.claim_id])) == [c]
    assert run(store.put_findings("r", [f])) == [f.finding_id]
    assert run(store.get_findings([f.finding_id])) == [f]


def test_claim_evidence는_run별로_격리된다():
    store = MemoryReviewStore()
    item = ClaimEvidence(claim_id=U(1), evidence_id=U(2), stance="neutral", stance_source="rule")
    run(store.put_claim_evidence("r1", [item]))
    run(store.put_claim_evidence("r2", [item]))
    assert run(store.get_claim_evidence("r1", item.claim_id)) == [item]
    assert run(store.get_claim_evidence("none", item.claim_id)) == []


def test_claim_evaluation은_run_claim별_current_upsert다():
    store = MemoryReviewStore()
    first = evaluation(2, U(1))
    second = evaluation(3, U(1))
    run(store.put_claim_evaluations("r", [first]))
    run(store.put_claim_evaluations("r", [second]))
    assert run(
        store.get_claim_evaluations([first.claim_evaluation_id, second.claim_evaluation_id])
    ) == [second]
    other = evaluation(4, U(1))
    run(store.put_claim_evaluations("other", [other]))
    assert run(
        store.get_claim_evaluations([second.claim_evaluation_id, other.claim_evaluation_id])
    ) == [second, other]
