from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.contexts.views import RenderCitationView
from app.orchestration.drafts import RenderDraft, RenderedSlotDraft
from app.schemas.frozen import CitationRef, NonBlankStr, SlotId, TheoryNote, Violation


class _Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RenderedSlotArtifact(_Artifact):
    slot_no: SlotId
    text: NonBlankStr
    citations: list[CitationRef]


class ReportArtifact(_Artifact):
    schema_version: Literal["s0.v1"] = "s0.v1"
    rendered_slots: list[RenderedSlotArtifact]
    banners: list[NonBlankStr]
    theory_notes: list[TheoryNote]
    citations: list[RenderCitationView]
    created_at: datetime


def deduplicate_citations(citations: list[CitationRef]) -> list[CitationRef]:
    """Remove exact (evidence_id, span) duplicates while preserving order."""
    seen: set[tuple[str, str]] = set()
    result: list[CitationRef] = []
    for citation in citations:
        key = (citation.evidence_id, citation.span)
        if key in seen:
            continue
        seen.add(key)
        result.append(citation)
    return result


def coalesce_rendered_slots(slots: list[RenderedSlotDraft]) -> list[RenderedSlotArtifact]:
    """Normalize duplicate slot numbers without dropping text or citations."""
    first_by_slot: dict[int, RenderedSlotDraft] = {}
    text_by_slot: dict[int, list[str]] = {}
    citations_by_slot: dict[int, list[CitationRef]] = {}
    for item in slots:
        if item.slot_no not in first_by_slot:
            first_by_slot[item.slot_no] = item
            text_by_slot[item.slot_no] = [item.text]
            citations_by_slot[item.slot_no] = []
        elif item.text not in text_by_slot[item.slot_no]:
            text_by_slot[item.slot_no].append(item.text)
        citations_by_slot[item.slot_no].extend(item.citations)

    return [
        RenderedSlotArtifact(
            slot_no=slot_no,
            text=" ".join(text_by_slot[slot_no]),
            citations=deduplicate_citations(citations_by_slot[slot_no]),
        )
        for slot_no in first_by_slot
    ]


class RenderCandidate(_Artifact):
    candidate: RenderDraft
    guard_feedback: tuple[Violation, ...] = ()
    rewrite_count: int = 0
    approved: bool = False


class RenderCandidateStore:
    def __init__(self) -> None:
        self._items: dict[str, RenderCandidate] = {}

    def put(self, run_id: str, candidate: RenderDraft) -> None:
        previous = self._items.get(run_id)
        self._items[run_id] = RenderCandidate(
            candidate=candidate,
            guard_feedback=() if previous is None else previous.guard_feedback,
            rewrite_count=0 if previous is None else previous.rewrite_count,
        )

    def get(self, run_id: str) -> RenderCandidate:
        return self._items[run_id]

    def contains(self, run_id: str) -> bool:
        return run_id in self._items

    def review(self, run_id: str, violations: list[Violation]) -> None:
        current = self.get(run_id)
        self._items[run_id] = current.model_copy(
            update={
                "guard_feedback": tuple(violations),
                "rewrite_count": current.rewrite_count + bool(violations),
                "approved": not violations,
            }
        )


def build_report_artifact(
    draft: RenderDraft,
    *,
    banners: list[str],
    theory_notes: list[TheoryNote],
    citation_views: dict[str, RenderCitationView],
    created_at: datetime,
) -> ReportArtifact:
    slots = coalesce_rendered_slots(draft.slots)
    keys = sorted({citation.evidence_id for item in slots for citation in item.citations})
    return ReportArtifact(
        rendered_slots=slots,
        banners=banners,
        theory_notes=theory_notes,
        citations=[citation_views[key] for key in keys],
        created_at=created_at,
    )
