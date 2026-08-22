"""Deterministic, ephemeral Judgment Review projections for n9 and n11."""

from collections.abc import Iterable, Mapping

from app.contexts.views import MissingSlotView, SlotTextView
from app.domain.slot_resolution import CurrentSlotProjection, CurrentSlotStatus
from app.orchestration.drafts import FindingDraft
from app.schemas.frozen import CitationRef, ClaimEvaluation, Finding, OpposeBlock


def build_missing_slot_views(
    projections: Iterable[CurrentSlotProjection],
) -> list[MissingSlotView]:
    views = []
    for projection in sorted(projections, key=lambda item: item.slot_id):
        if projection.slot_id not in {7, 8} or projection.status is CurrentSlotStatus.RESOLVED:
            continue
        if projection.status is CurrentSlotStatus.ABSENT:
            status = "absent"
            summary = "사용자 응답이 없습니다."
        else:
            status = "conflict"
            summary = "현재 사용자 응답을 하나의 값으로 확정할 수 없습니다."
        views.append(
            MissingSlotView(
                slot_id=projection.slot_id,
                status=status,
                summary=summary,
            )
        )
    return views


def build_judgment_review_drafts(
    *,
    evaluations: Iterable[ClaimEvaluation],
    oppose: OpposeBlock,
    counter_claim_ids: set[str],
    projections: Iterable[CurrentSlotProjection],
    citation_by_evidence_id: Mapping[str, CitationRef] | None = None,
) -> list[FindingDraft]:
    evaluations = sorted(evaluations, key=lambda item: item.claim_evaluation_id)
    projection_by_slot = {item.slot_id: item for item in projections}
    citations = dict(citation_by_evidence_id or {})
    for evaluation in evaluations:
        citations.update({item.evidence_id: item for item in evaluation.citations})

    drafts: list[FindingDraft] = []
    if oppose.status == "verified" and oppose.count:
        for evaluation in evaluations:
            if evaluation.claim_id not in counter_claim_ids:
                continue
            oppose_citations = [
                citations[evidence_id]
                for evidence_id in evaluation.oppose_evidence_ids
                if evidence_id in citations
            ]
            if oppose_citations:
                drafts.append(
                    FindingDraft(
                        slot_id=7,
                        kind="mismatch",
                        citations=oppose_citations,
                        claim_evaluation_id=evaluation.claim_evaluation_id,
                    )
                )
    elif oppose.status == "unverified" and oppose.queries:
        drafts.extend(
            FindingDraft(
                slot_id=7,
                kind="unverified",
                citations=[],
                claim_evaluation_id=evaluation.claim_evaluation_id,
            )
            for evaluation in evaluations
            if evaluation.claim_id in counter_claim_ids
        )

    slot8 = projection_by_slot.get(8)
    if slot8 is not None and slot8.status is CurrentSlotStatus.ABSENT:
        drafts.append(
            FindingDraft(
                slot_id=8,
                kind="missing",
                citations=[],
                claim_evaluation_id=None,
            )
        )
    return drafts


def build_review_slot_views(findings: Iterable[Finding]) -> list[SlotTextView]:
    messages = {
        (7, "mismatch"): (
            "현재 판단과 반대되는 근거도 확인되었습니다. 기존 근거와 함께 비교해볼 필요가 있습니다."
        ),
        (7, "unverified"): "반대 방향 근거 검증이 완료되지 않았습니다.",
        (8, "missing"): "현재 판단을 다시 검토할 조건이 명확하지 않습니다.",
    }
    return [
        SlotTextView(
            slot_no=finding.slot_id,
            text=messages.get(
                (finding.slot_id, finding.kind),
                "현재 확인된 근거와 사용자 입력을 함께 다시 점검할 필요가 있습니다.",
            ),
            quoted=False,
            citations=finding.citations,
        )
        for finding in sorted(
            findings,
            key=lambda item: (item.slot_id, item.kind, item.finding_id),
        )
    ]
