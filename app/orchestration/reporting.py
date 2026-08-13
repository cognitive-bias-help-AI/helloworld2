from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.contexts.views import RenderCitationView
from app.orchestration.drafts import RenderDraft
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
    slots = [RenderedSlotArtifact(**item.model_dump()) for item in draft.slots]
    keys = sorted({citation.evidence_id for item in slots for citation in item.citations})
    return ReportArtifact(
        rendered_slots=slots,
        banners=banners,
        theory_notes=theory_notes,
        citations=[citation_views[key] for key in keys],
        created_at=created_at,
    )
