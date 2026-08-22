from datetime import UTC, datetime

from app.domain.intake import ResponseState
from app.domain.slot_resolution import CurrentSlotProjection, CurrentSlotStatus
from app.orchestration.judgment_review import (
    build_judgment_review_drafts,
    build_missing_slot_views,
    build_review_slot_views,
    build_slot_projection_review_views,
)
from app.schemas.frozen import CitationRef, ClaimEvaluation, Finding, OpposeBlock, ReasonCode

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def projection(slot_id: int, status: CurrentSlotStatus) -> CurrentSlotProjection:
    return CurrentSlotProjection(
        slot_id=slot_id,
        status=status,
        values=("known",) if status is CurrentSlotStatus.RESOLVED else (),
        issue_ids=(f"issue-{slot_id}",)
        if status in {CurrentSlotStatus.CONFLICT, CurrentSlotStatus.AMBIGUOUS}
        else (),
        response_state=(
            ResponseState.ANSWERED
            if status is CurrentSlotStatus.RESOLVED
            else ResponseState.UNKNOWN
        ),
    )


def evaluation(*, oppose_ids=(), support_ids=()) -> ClaimEvaluation:
    oppose = list(oppose_ids)
    support = list(support_ids)
    ids = [*oppose, *support]
    return ClaimEvaluation(
        claim_evaluation_id=uid(1),
        claim_id=uid(2),
        citations=[
            CitationRef(evidence_id=item, span=f"span-{index}") for index, item in enumerate(ids)
        ],
        support_evidence_ids=support,
        oppose_evidence_ids=oppose,
        neutral_evidence_ids=[],
        unknown_evidence_ids=[],
        numeric_checks=[],
        verdict="contradicted" if oppose else "support" if support else "unverifiable",
        missing_dimensions=[],
        uncertainty_codes=[],
        created_at=NOW,
    )


def test_slot8_absent_creates_missing_review_but_resolved_does_not():
    absent = build_judgment_review_drafts(
        evaluations=[],
        oppose=OpposeBlock(status="unverified", reason=ReasonCode.EVIDENCE_INSUFFICIENT),
        counter_claim_ids=set(),
        projections=[
            projection(7, CurrentSlotStatus.RESOLVED),
            projection(8, CurrentSlotStatus.ABSENT),
        ],
    )
    resolved = build_judgment_review_drafts(
        evaluations=[],
        oppose=OpposeBlock(status="unverified", reason=ReasonCode.EVIDENCE_INSUFFICIENT),
        counter_claim_ids=set(),
        projections=[
            projection(7, CurrentSlotStatus.RESOLVED),
            projection(8, CurrentSlotStatus.RESOLVED),
        ],
    )
    assert [(item.slot_id, item.kind, item.citations) for item in absent] == [(8, "missing", [])]
    assert resolved == []


def test_slot8_conflict_and_ambiguity_are_not_collapsed_to_missing():
    for status in (CurrentSlotStatus.CONFLICT, CurrentSlotStatus.AMBIGUOUS):
        projections = [projection(7, CurrentSlotStatus.RESOLVED), projection(8, status)]
        views = build_missing_slot_views(projections)
        drafts = build_judgment_review_drafts(
            evaluations=[],
            oppose=OpposeBlock(status="unverified", reason=ReasonCode.EVIDENCE_INSUFFICIENT),
            counter_claim_ids=set(),
            projections=projections,
        )
        assert [(item.slot_id, item.status) for item in views] == [(8, "conflict")]
        assert not any(item.kind == "missing" for item in drafts)


def test_verified_oppose_builds_citation_backed_review_and_zero_does_not():
    evidence_id = uid(3)
    item = evaluation(oppose_ids=[evidence_id])
    projections = [
        projection(7, CurrentSlotStatus.ABSENT),
        projection(8, CurrentSlotStatus.RESOLVED),
    ]
    positive = build_judgment_review_drafts(
        evaluations=[item],
        oppose=OpposeBlock(status="verified", count=1, queries=["005930"]),
        counter_claim_ids={item.claim_id},
        projections=projections,
    )
    zero = build_judgment_review_drafts(
        evaluations=[item],
        oppose=OpposeBlock(status="verified", count=0, queries=["005930"]),
        counter_claim_ids={item.claim_id},
        projections=projections,
    )
    assert [(draft.kind, draft.citations[0].evidence_id) for draft in positive] == [
        ("mismatch", evidence_id)
    ]
    assert zero == []


def test_unverified_counter_search_is_not_treated_as_no_opposing_evidence():
    item = evaluation()
    drafts = build_judgment_review_drafts(
        evaluations=[item],
        oppose=OpposeBlock(
            status="unverified", reason=ReasonCode.UPSTREAM_TIMEOUT, queries=["005930"]
        ),
        counter_claim_ids={item.claim_id},
        projections=[
            projection(7, CurrentSlotStatus.ABSENT),
            projection(8, CurrentSlotStatus.RESOLVED),
        ],
    )
    assert [(draft.kind, draft.claim_evaluation_id, draft.citations) for draft in drafts] == [
        ("unverified", item.claim_evaluation_id, [])
    ]


def test_review_slot_views_keep_external_citation_only_on_counter_review():
    evidence_id = uid(3)
    item = evaluation(oppose_ids=[evidence_id])
    drafts = build_judgment_review_drafts(
        evaluations=[item],
        oppose=OpposeBlock(status="verified", count=1, queries=["005930"]),
        counter_claim_ids={item.claim_id},
        projections=[
            projection(7, CurrentSlotStatus.ABSENT),
            projection(8, CurrentSlotStatus.ABSENT),
        ],
    )
    from app.assemblers.findings import assemble_findings

    findings = assemble_findings(drafts, [item], [uid(10), uid(11)], NOW)
    views = build_review_slot_views(findings, [item])
    by_slot = {view.slot_no: view for view in views}
    assert by_slot[7].citations[0].evidence_id == evidence_id
    assert by_slot[8].citations == []
    assert "반대되는 근거" in by_slot[7].text
    assert "다시 검토할 조건" in by_slot[8].text


def test_support_mismatch_is_not_rendered_as_counter_evidence():
    evidence_id = uid(4)
    item = evaluation(support_ids=[evidence_id])
    finding = Finding(
        finding_id=uid(20),
        slot_id=7,
        kind="mismatch",
        citations=[CitationRef(evidence_id=evidence_id, span="span-0")],
        claim_evaluation_id=item.claim_evaluation_id,
        created_at=NOW,
    )

    view = build_review_slot_views([finding], [item])[0]

    assert "반대되는 근거" not in view.text
    assert view.citations[0].evidence_id == evidence_id


def test_mismatch_with_unknown_evaluation_reference_fails_closed():
    finding = Finding(
        finding_id=uid(21),
        slot_id=7,
        kind="mismatch",
        citations=[CitationRef(evidence_id=uid(5), span="span")],
        claim_evaluation_id=uid(999),
        created_at=NOW,
    )

    import pytest

    with pytest.raises(ValueError, match="unknown ClaimEvaluation"):
        build_review_slot_views([finding], [evaluation()])


def test_slot8_conflict_and_ambiguity_have_distinct_ephemeral_render_views():
    conflict = build_slot_projection_review_views(
        [projection(8, CurrentSlotStatus.CONFLICT)]
    )
    ambiguous = build_slot_projection_review_views(
        [projection(8, CurrentSlotStatus.AMBIGUOUS)]
    )
    absent = build_slot_projection_review_views(
        [projection(8, CurrentSlotStatus.ABSENT)]
    )

    assert "서로 다른 내용" in conflict[0].text
    assert "의미가 명확하지 않아" in ambiguous[0].text
    assert conflict[0].citations == ambiguous[0].citations == []
    assert absent == []
