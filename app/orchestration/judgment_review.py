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


def build_review_slot_views(
    findings: Iterable[Finding],
    evaluations: Iterable[ClaimEvaluation],
) -> list[SlotTextView]:
    evaluation_by_id = {
        item.claim_evaluation_id: item for item in evaluations
    }
    messages = {
        (7, "unverified"): "반대 방향 근거 검증이 완료되지 않았습니다.",
        (8, "missing"): "현재 판단을 다시 검토할 조건이 명확하지 않습니다.",
    }
    # Slot 별 문구가 없을 때 kind 로 한 번 더 받는다.
    #
    # 근거 0건에도 Report 를 내보내기로 하면서 slot 4·5 의 unverified Finding 이
    # 실제로 렌더링되기 시작했다. 그때 기본 문구("확인된 근거와 사용자 입력을
    # 함께 다시 점검")가 나가면 **확인된 근거가 있는 것처럼 읽힌다.**
    # 확인하지 못한 것을 확인한 것처럼 쓰지 않는 것이 이 시스템의 요점이다.
    by_kind = {
        "unverified": "이 부분은 현재 확인되지 않았습니다.",
        "missing": "판단에 필요한 내용이 확인되지 않았습니다.",
        "conflict": "입력하신 내용 안에 서로 다른 부분이 있어 하나로 확정하기 어렵습니다.",
    }
    views = []
    for finding in sorted(
            findings,
            key=lambda item: (item.slot_id, item.kind, item.finding_id),
        ):
        evaluation = None
        if finding.claim_evaluation_id is not None:
            evaluation = evaluation_by_id.get(finding.claim_evaluation_id)
            if evaluation is None:
                raise ValueError("Finding references an unknown ClaimEvaluation")
        is_counter_mismatch = (
            finding.slot_id == 7
            and finding.kind == "mismatch"
            and evaluation is not None
            and bool(
                {item.evidence_id for item in finding.citations}
                & set(evaluation.oppose_evidence_ids)
            )
        )
        text = (
            "현재 판단과 반대되는 근거도 확인되었습니다. 기존 근거와 함께 비교해볼 필요가 있습니다."
            if is_counter_mismatch
            else messages.get(
                (finding.slot_id, finding.kind),
                by_kind.get(
                    finding.kind,
                    "현재 확인된 근거와 사용자 입력을 함께 다시 점검할 필요가 있습니다.",
                ),
            )
        )
        views.append(
            SlotTextView(
                slot_no=finding.slot_id,
                text=text,
                quoted=False,
                citations=finding.citations,
            )
        )
    return views


def coalesce_slot_text_views(views: Iterable[SlotTextView]) -> list[SlotTextView]:
    """Coalesce review text by slot while preserving first-seen order."""
    grouped: dict[int, SlotTextView] = {}
    text_by_slot: dict[int, list[str]] = {}
    citation_keys_by_slot: dict[int, set[tuple[str, str]]] = {}

    for view in views:
        slot_no = view.slot_no
        if slot_no not in grouped:
            grouped[slot_no] = SlotTextView(
                slot_no=slot_no,
                text=view.text,
                quoted=view.quoted,
                citations=[],
            )
            text_by_slot[slot_no] = [view.text]
            citation_keys_by_slot[slot_no] = set()
        else:
            if view.text not in text_by_slot[slot_no]:
                text_by_slot[slot_no].append(view.text)

        citations = grouped[slot_no].citations
        seen = citation_keys_by_slot[slot_no]
        for citation in view.citations:
            key = (citation.evidence_id, citation.span)
            if key in seen:
                continue
            seen.add(key)
            citations.append(citation)

    return [
        grouped[slot_no].model_copy(update={"text": " ".join(text_by_slot[slot_no])})
        for slot_no in grouped
    ]


def build_slot_projection_review_views(
    projections: Iterable[CurrentSlotProjection],
) -> list[SlotTextView]:
    messages = {
        CurrentSlotStatus.CONFLICT: (
            "현재 제시된 판단 변경 조건에 서로 다른 내용이 있어 하나의 조건으로 확정하기 어렵습니다."
        ),
        CurrentSlotStatus.AMBIGUOUS: (
            "현재 제시된 판단 변경 조건의 의미가 명확하지 않아 하나의 조건으로 확정하기 어렵습니다."
        ),
    }
    return [
        SlotTextView(
            slot_no=8,
            text=messages[projection.status],
            quoted=False,
            citations=[],
        )
        for projection in projections
        if projection.slot_id == 8 and projection.status in messages
    ]
