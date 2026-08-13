from __future__ import annotations

from datetime import UTC, datetime

from app.contexts.budget import ctx_chars
from app.contexts.views import EvidencePacket, IntegrationView, RenderView, VerifyPacket
from app.gateway.adapters.mock import MockAdapter
from app.orchestration.drafts import (
    AskBackDraft,
    AskBackQuestionDraft,
    ExtractedClaimDraft,
    FindingDraft,
    GuardScanResult,
    GuardVerdictDraft,
    RenderDraft,
    RenderedSlotDraft,
    SlotExtractionDraft,
)
from app.orchestration.reporting import RenderCandidateStore
from app.orchestration.runtime import RuntimeDeps
from app.schemas.frozen import (
    CitationRef,
    ClaimEvaluationDraft,
    ClaimEvidenceDraft,
    ClaimStanceDraft,
    StockCandidate,
    Usage,
)
from app.store.memory_evidence_store import MemoryEvidenceStore
from app.store.memory_review_store import MemoryReviewStore
from tests.s0.fakes import FixtureStockResolver

NOW = datetime(2026, 8, 14, tzinfo=UTC)
RAW = "삼성전자 영업이익이 증가했다"


class Ids:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return f"01ARZ3NDEKTSV4RRFFQ69G{self.value:04d}"


class FlowGateway:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        node = prompt_version.split("/", 1)[0]
        self.calls.append((node, input_view))
        if output_schema is GuardScanResult:
            value = GuardScanResult()
        elif output_schema is SlotExtractionDraft:
            value = SlotExtractionDraft(
                claims=[
                    ExtractedClaimDraft(
                        slot_id=1,
                        user_text_span=RAW,
                        span_offset=(0, len(RAW)),
                        normalized_proposition="영업이익 증가",
                        verifiable=True,
                    )
                ]
            )
        elif output_schema is AskBackDraft:
            value = AskBackDraft(
                questions=[AskBackQuestionDraft(slot_id=1, question="검증할 주장은 무엇인가요?")]
            )
        elif output_schema is ClaimStanceDraft:
            assert isinstance(input_view, EvidencePacket)
            value = ClaimStanceDraft(
                stances=[
                    ClaimEvidenceDraft(evidence_id=item.evidence_id, stance="support")
                    for item in input_view.evidence
                ]
            )
        elif output_schema is ClaimEvaluationDraft:
            assert isinstance(input_view, VerifyPacket)
            ids = [item.evidence_id for item in input_view.evidence]
            value = ClaimEvaluationDraft(
                citations=[CitationRef(evidence_id=ids[0], span=input_view.evidence[0].raw_span)],
                support_evidence_ids=ids,
                oppose_evidence_ids=[],
                unknown_evidence_ids=[],
                verdict="support",
                missing_dimensions=[],
                uncertainty_codes=[],
            )
        elif output_schema is FindingDraft:
            assert isinstance(input_view, IntegrationView)
            evaluation = input_view.evaluations[0]
            citation = evaluation.citations[0]
            value = FindingDraft(
                slot_id=1,
                kind="unverified",
                citations=[citation],
                claim_evaluation_id=evaluation.claim_evaluation_id,
            )
        elif output_schema is RenderDraft:
            assert isinstance(input_view, RenderView)
            citation = CitationRef(
                evidence_id=input_view.citations[0].evidence_id,
                span=input_view.citations[0].span,
            )
            value = RenderDraft(
                slots=[RenderedSlotDraft(slot_no=1, text="검증된 결과", citations=[citation])]
            )
        elif output_schema is GuardVerdictDraft:
            value = GuardVerdictDraft(violations=[])
        else:
            raise AssertionError(output_schema)
        return value, Usage(
            model_slot=slot,
            prompt_tokens=0,
            output_tokens=0,
            ctx_chars=ctx_chars(input_view),
        )


def initial_state():
    return {
        "run_id": "run-s0",
        "thread_id": "thread-s0",
        "as_of": NOW.isoformat(),
        "snapshot_version": 0,
        "input_id": None,
        "stock": None,
        "user_action": None,
        "slots": [],
        "claim_ids": [],
        "conflicts": [],
        "query_ids": [],
        "collections": {},
        "claim_evaluation_ids": [],
        "finding_ids": [],
        "oppose": None,
        "report_id": None,
        "node_results": [],
        "counters": {},
        "started_at": NOW.isoformat(),
    }


def deps(gateway=None, resolver=None):
    gateway = gateway or FlowGateway()
    resolver = resolver or FixtureStockResolver(
        {
            RAW: [
                StockCandidate(
                    code="005930",
                    name="삼성전자",
                    market="KOSPI",
                    match_kind="exact_name",
                    score=1.0,
                )
            ]
        }
    )
    return RuntimeDeps(
        review_store=MemoryReviewStore(),
        evidence_store=MemoryEvidenceStore(),
        model_gateway=gateway,
        stock_resolver=resolver,
        adapters={"dart": MockAdapter("dart")},
        clock=lambda: NOW,
        id_factory=Ids(),
        render_candidates=RenderCandidateStore(),
    )
