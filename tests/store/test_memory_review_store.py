import asyncio
import copy
from datetime import UTC, datetime

import pytest

from app.domain.intake import ResponseState
from app.domain.semantic_source import SemanticTextRef
from app.domain.slot_context import ExtractionMethod, build_slot_observation
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


def test_ReviewStore_14개_method와_7개_persistence_area를_왕복한다():
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


def observation(run_id="r", *, value="LONG", origin=SourceTrace.SURVEY, text_ref=None):
    return build_slot_observation(
        run_id,
        slot_id=3,
        response_state=ResponseState.ANSWERED,
        origin=origin,
        extraction_method=(
            ExtractionMethod.LLM
            if origin is SourceTrace.CHAT_EXPLICIT
            else ExtractionMethod.DIRECT
        ),
        value=value,
        text_ref=text_ref,
    )


def test_put_input은_same_run_same_canonical_body만_exact_replay한다():
    store = MemoryReviewStore()
    body = {"nested": {"values": [1, 2]}, "mode": "HYBRID"}

    first = run(store.put_input("r", body))
    second = run(
        store.put_input("r", {"mode": "HYBRID", "nested": {"values": [1, 2]}})
    )

    assert first == second
    with pytest.raises(ValueError, match="input run/payload conflict"):
        run(store.put_input("r", {"nested": {"values": [1, 3]}, "mode": "HYBRID"}))


def test_put_input과_get_input은_nested_body를_deep_isolate한다():
    store = MemoryReviewStore()
    body = {"nested": {"values": [1, 2]}}
    expected = copy.deepcopy(body)

    input_id = run(store.put_input("r", body))
    body["nested"]["values"].append(3)
    fetched = run(store.get_input(input_id))
    fetched["nested"]["values"].append(4)

    assert run(store.get_input(input_id)) == expected


def test_slot_observation_exact_replay는_duplicate없이_삽입순서를_보존한다():
    store = MemoryReviewStore()
    survey = observation()
    chat = observation(
        origin=SourceTrace.CHAT_EXPLICIT,
        text_ref=SemanticTextRef(
            segment_id="free_text:0", local_start=0, local_end=4
        ),
    )

    assert run(store.put_slot_observations("r", [chat, survey])) == [
        chat.observation_id,
        survey.observation_id,
    ]
    assert run(store.put_slot_observations("r", [chat, survey])) == [
        chat.observation_id,
        survey.observation_id,
    ]
    assert run(store.get_slot_observations("r")) == [chat, survey]


def test_slot_observation_ID_payload_run_hash_conflict를_모두_거부한다():
    store = MemoryReviewStore()
    item = observation()
    run(store.put_slot_observations("r", [item]))

    changed_body = item.model_copy(update={"value": "SHORT"})
    with pytest.raises(ValueError, match="observation_id ownership/payload conflict"):
        run(store.put_slot_observations("r", [changed_body]))

    with pytest.raises(ValueError, match="observation_id ownership/payload conflict"):
        run(store.put_slot_observations("other", [item]))

    wrong_id = item.model_copy(update={"observation_id": U(8)})
    with pytest.raises(ValueError, match="content hash/ID conflict"):
        run(store.put_slot_observations("r", [wrong_id]))


def test_slot_observation_batch_conflict는_partial_write를_남기지_않는다():
    store = MemoryReviewStore()
    existing = observation()
    run(store.put_slot_observations("r", [existing]))
    new_item = observation(value="SHORT")
    conflict = existing.model_copy(update={"value": "MEDIUM"})

    with pytest.raises(ValueError, match="observation_id ownership/payload conflict"):
        run(store.put_slot_observations("r", [new_item, conflict]))

    assert run(store.get_slot_observations("r")) == [existing]


def test_semantic_batch은_observation과_claim을_함께_저장하고_exact_replay한다():
    store = MemoryReviewStore()
    item = observation()
    semantic_claim = claim(2)

    first = run(store.put_semantic_batch("r", [item], [semantic_claim]))
    replay = run(store.put_semantic_batch("r", [item], [semantic_claim]))

    assert first == replay == ([item.observation_id], [semantic_claim.claim_id])
    assert run(store.get_slot_observations("r")) == [item]
    assert run(store.get_claims([semantic_claim.claim_id])) == [semantic_claim]


def test_semantic_batch은_context_only_observation만_저장할_수_있다():
    store = MemoryReviewStore()
    item = observation()

    assert run(store.put_semantic_batch("r", [item], [])) == ([item.observation_id], [])
    assert run(store.get_slot_observations("r")) == [item]


def test_semantic_batch_observation_conflict는_claim_partial_write를_남기지_않는다():
    store = MemoryReviewStore()
    existing = observation()
    conflicting = existing.model_copy(update={"value": "SHORT"})
    semantic_claim = claim(2)
    run(store.put_slot_observations("r", [existing]))

    with pytest.raises(ValueError, match="observation_id ownership/payload conflict"):
        run(store.put_semantic_batch("r", [conflicting], [semantic_claim]))

    assert run(store.get_slot_observations("r")) == [existing]
    with pytest.raises(KeyError):
        run(store.get_claims([semantic_claim.claim_id]))


def test_semantic_batch_claim_conflict는_observation_partial_write를_남기지_않는다():
    store = MemoryReviewStore()
    existing = claim()
    conflicting = existing.model_copy(update={"normalized_proposition": "changed"})
    item = observation()
    run(store.put_claims("r", [existing]))

    with pytest.raises(ValueError, match="claim_id ownership/payload conflict"):
        run(store.put_semantic_batch("r", [item], [conflicting]))

    assert run(store.get_slot_observations("r")) == []
    assert run(store.get_claims([existing.claim_id])) == [existing]


def test_semantic_batch은_cross_run과_internal_conflict에서_all_or_none이다():
    store = MemoryReviewStore()
    item = observation()
    semantic_claim = claim(2)
    run(store.put_claims("other", [semantic_claim]))

    with pytest.raises(ValueError, match="claim_id ownership/payload conflict"):
        run(store.put_semantic_batch("r", [item], [semantic_claim]))
    assert run(store.get_slot_observations("r")) == []
    assert run(store.get_claims([semantic_claim.claim_id])) == [semantic_claim]

    first = observation(value="SHORT")
    conflicting = first.model_copy(update={"value": "MEDIUM"})
    with pytest.raises(ValueError, match="observation_id ownership/payload conflict"):
        run(store.put_semantic_batch("r", [first, conflicting], [semantic_claim]))
    assert run(store.get_slot_observations("r")) == []
