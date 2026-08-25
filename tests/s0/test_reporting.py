from datetime import UTC, datetime

import pytest

from app.contexts.views import RenderCitationView
from app.orchestration.drafts import RenderDraft, RenderedSlotDraft
from app.orchestration.reporting import (
    RenderCandidateStore,
    ReportArtifact,
    build_report_artifact,
)
from app.orchestration.validators.citations import CitationContractViolation, validate_citations
from app.schemas.frozen import CitationRef, Evidence, ReasonCode
from app.ui_projection import _report

NOW = datetime(2026, 8, 14, tzinfo=UTC)
EID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def citation(span="문장"):
    return CitationRef(evidence_id=EID, span=span)


def evidence(raw_span="원문 문장 입니다"):
    return Evidence(
        evidence_id=EID, source_type="news", source_ref="ref", source_url="https://example.com",
        publisher="p", published_at=NOW, fetched_at=NOW, raw_span=raw_span,
        span_scope="headline_snippet", content_sha256="a" * 64,
        provider_request_id="01ARZ3NDEKTSV4RRFFQ69G5FAW", as_of=NOW,
    )


def test_render_candidate_store_is_run_isolated():
    store = RenderCandidateStore()
    a = RenderDraft(slots=[RenderedSlotDraft(slot_no=1, text="A", citations=[])])
    b = RenderDraft(slots=[RenderedSlotDraft(slot_no=1, text="B", citations=[])])
    store.put("a", a)
    store.put("b", b)
    assert store.get("a").candidate.slots[0].text == "A"
    assert store.get("b").candidate.slots[0].text == "B"


def test_report_artifact_converts_draft_and_derives_citation_index():
    draft = RenderDraft(slots=[RenderedSlotDraft(slot_no=1, text="본문", citations=[citation(), citation()])])
    views = {EID: RenderCitationView(evidence_id=EID, span="문장", source_url="https://example.com", publisher="p")}
    artifact = build_report_artifact(draft, banners=["배너"], theory_notes=[], citation_views=views, created_at=NOW)
    assert isinstance(artifact, ReportArtifact)
    assert artifact.rendered_slots[0].__class__.__name__ == "RenderedSlotArtifact"
    assert [item.evidence_id for item in artifact.citations] == [EID]
    assert ReportArtifact.model_validate(artifact.model_dump(mode="json")) == artifact


def test_report_artifact_deduplicates_exact_citations_within_one_slot():
    draft = RenderDraft(
        slots=[
            RenderedSlotDraft(
                slot_no=1,
                text="본문",
                citations=[citation(), citation(), CitationRef(evidence_id=EID, span="다른 근거")],
            )
        ]
    )
    views = {
        EID: RenderCitationView(evidence_id=EID, span="문장", source_url="https://example.com", publisher="p")
    }
    artifact = build_report_artifact(
        draft, banners=[], theory_notes=[], citation_views=views, created_at=NOW
    )
    assert artifact.rendered_slots[0].citations == [citation(), CitationRef(evidence_id=EID, span="다른 근거")]


def test_report_artifact_keeps_same_citation_in_different_slots():
    draft = RenderDraft(
        slots=[
            RenderedSlotDraft(slot_no=1, text="A", citations=[citation(), citation()]),
            RenderedSlotDraft(slot_no=2, text="B", citations=[citation()]),
        ]
    )
    views = {
        EID: RenderCitationView(evidence_id=EID, span="문장", source_url="https://example.com", publisher="p")
    }
    artifact = build_report_artifact(
        draft, banners=[], theory_notes=[], citation_views=views, created_at=NOW
    )
    assert artifact.rendered_slots[0].citations == [citation()]
    assert artifact.rendered_slots[1].citations == [citation()]


def test_report_artifact_keeps_top_level_citation_index_unique():
    draft = RenderDraft(
        slots=[
            RenderedSlotDraft(slot_no=1, text="A", citations=[citation()]),
            RenderedSlotDraft(slot_no=2, text="B", citations=[citation()]),
        ]
    )
    views = {
        EID: RenderCitationView(evidence_id=EID, span="문장", source_url="https://example.com", publisher="p")
    }
    artifact = build_report_artifact(
        draft, banners=[], theory_notes=[], citation_views=views, created_at=NOW
    )
    assert len(artifact.citations) == 1


def test_ui_projection_preserves_the_deduplicated_canonical_slot():
    draft = RenderDraft(
        slots=[RenderedSlotDraft(slot_no=1, text="A", citations=[citation(), citation()])]
    )
    views = {
        EID: RenderCitationView(evidence_id=EID, span="문장", source_url="https://example.com", publisher="p")
    }
    artifact = build_report_artifact(
        draft, banners=[], theory_notes=[], citation_views=views, created_at=NOW
    )
    projected = _report(artifact)
    assert projected["renderedSlots"][0]["citations"] == [{"evidenceId": EID, "span": "문장"}]


def test_i7_exact_containment_and_no_normalization():
    validate_citations([citation()], {EID: evidence()})
    for bad in [CitationRef(evidence_id=EID, span="원문  문장"), CitationRef(evidence_id="01ARZ3NDEKTSV4RRFFQ69G5FAX", span="문장")]:
        with pytest.raises(CitationContractViolation) as caught:
            validate_citations([bad], {EID: evidence()})
        assert caught.value.reason_code is ReasonCode.CONTRACT_VIOLATION
