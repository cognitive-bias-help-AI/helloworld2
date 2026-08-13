"""P0-3 Context budget와 양 끝점 절단 계약 회귀."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from app.contexts.budget import (
    NODE_BUDGETS,
    ctx_chars,
    ctx_items,
    truncate,
    validate_evidence_counts,
)
from app.contexts.views import (
    AskBackContext,
    ClaimView,
    ClassifiedEvidenceView,
    EvidenceExcerptView,
    EvidencePacket,
    GuardBatchEnvelope,
    GuardScanView,
    IntegrationView,
    MissingSlotView,
    RenderView,
    SlotContext,
    SlotDefinitionView,
    VerifyPacket,
)
from app.schemas.frozen import GuardInput, OpposeBlock

BASE = datetime(2026, 8, 13, tzinfo=UTC)
CLAIM_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
EVIDENCE_IDS = [
    "01ARZ3NDEKTSV4RRFFQ69G5FAW",
    "01ARZ3NDEKTSV4RRFFQ69G5FAX",
    "01ARZ3NDEKTSV4RRFFQ69G5FAY",
    "01ARZ3NDEKTSV4RRFFQ69G5FAZ",
]


def claim() -> ClaimView:
    return ClaimView(claim_id=CLAIM_ID, slot_id=1, normalized_proposition="매출 증가")


def evidence(index: int, *, day: int | None = None) -> EvidenceExcerptView:
    return EvidenceExcerptView(
        evidence_id=EVIDENCE_IDS[index],
        source_type="news",
        source_ref=f"article-{index}",
        as_of=BASE + timedelta(days=index if day is None else day),
        raw_span=f"근거 {index}",
    )


def test_NODE_BUDGETS는_DDR의_8개_고정값이다():
    assert {node: (budget.items, budget.chars) for node, budget in NODE_BUDGETS.items()} == {
        "n1": (None, 2000),
        "n3": (8, 6000),
        "n4": (2, 1500),
        "n7": (12, 4000),
        "n8": (12, 4500),
        "n9": (8, 5000),
        "n10": (8, 3000),
        "n11": (8, 3500),
    }
    with pytest.raises(FrozenInstanceError):
        NODE_BUDGETS["n1"].chars = 1


def test_ctx_chars는_exclude_none_JSON_payload_문자수를_센다():
    short = GuardScanView(masked_input="판단")
    long = GuardScanView(masked_input="더 긴 투자 판단")

    assert ctx_chars(short) == len(short.model_dump_json(exclude_none=True))
    assert ctx_chars(short) == ctx_chars(short)
    assert ctx_chars(long) > ctx_chars(short)


def test_ctx_items는_노드별_반복_단위만_세고_Claim은_제외한다():
    definition = SlotDefinitionView(slot_id=1, name="목표", description="설명")
    missing = MissingSlotView(slot_id=1, status="absent", summary="없음")
    excerpt = evidence(0)
    classified = ClassifiedEvidenceView(**excerpt.model_dump(), stance="oppose")
    guard = GuardInput(slot_no=1, text="문장", quoted=False, citations=[])

    assert ctx_items(GuardScanView(masked_input="판단")) == 0
    assert ctx_items(SlotContext(masked_input="판단", slot_definitions=[definition])) == 1
    assert ctx_items(AskBackContext(missing_slots=[missing])) == 1
    assert ctx_items(EvidencePacket(claim=claim(), evidence=[excerpt])) == 1
    assert ctx_items(
        VerifyPacket(claim=claim(), evidence=[classified], numeric_checks=[])
    ) == 1
    assert ctx_items(
        IntegrationView(
            evaluations=[],
            oppose=OpposeBlock(status="verified", count=0, queries=["검색"]),
            missing_slots=[],
        )
    ) == 0
    assert ctx_items(GuardBatchEnvelope(items=[guard])) == 1
    assert ctx_items(RenderView(slots=[], banners=[], theory_notes=[], citations=[])) == 0


@pytest.mark.parametrize("claim_count,stock_count", [(9, 3), (0, 0)])
def test_Evidence_9_plus_3_상한은_허용한다(claim_count, stock_count):
    validate_evidence_counts(claim_count=claim_count, stock_count=stock_count)


@pytest.mark.parametrize("claim_count,stock_count", [(10, 0), (0, 4), (9, 4)])
def test_Evidence_claim_9_stock_3_total_12_초과는_거부한다(claim_count, stock_count):
    with pytest.raises(ValueError):
        validate_evidence_counts(claim_count=claim_count, stock_count=stock_count)


@pytest.mark.parametrize("limit", [0, -1])
def test_truncate는_0_이하_limit을_거부한다(limit):
    with pytest.raises(ValueError):
        truncate([], limit)


def test_truncate는_empty와_limit_이하를_시간순으로_보존한다():
    assert truncate([], 3) == ([], 0)
    items = [evidence(2), evidence(0), evidence(1)]

    kept, dropped = truncate(items, 3)

    assert [item.evidence_id for item in kept] == EVIDENCE_IDS[:3]
    assert dropped == 0


def test_truncate_limit_1은_가장_오래된_항목만_보존한다():
    kept, dropped = truncate([evidence(3), evidence(1), evidence(0), evidence(2)], 1)

    assert [item.evidence_id for item in kept] == [EVIDENCE_IDS[0]]
    assert dropped == 3


def test_truncate_초과는_최오래_1개와_최신_limit_minus_1개를_ID순으로_반환한다():
    kept, dropped = truncate([evidence(2), evidence(0), evidence(3), evidence(1)], 3)

    assert [item.evidence_id for item in kept] == [
        EVIDENCE_IDS[0],
        EVIDENCE_IDS[2],
        EVIDENCE_IDS[3],
    ]
    assert dropped == 1


def test_truncate의_최종_ID정렬은_시간순과_ID순이_달라도_결정적이다():
    items = [
        evidence(2, day=0),
        evidence(0, day=1),
        evidence(3, day=2),
        evidence(1, day=3),
    ]

    kept, dropped = truncate(items, 3)

    assert [item.evidence_id for item in kept] == [
        EVIDENCE_IDS[1],
        EVIDENCE_IDS[2],
        EVIDENCE_IDS[3],
    ]
    assert dropped == 1
