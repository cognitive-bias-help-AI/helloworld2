"""Evidence packing 계약 (B3).

실측 근거: 검색모듈 골든셋 377건/12종목에서 귀속 통과 문서가 종목당
최소 13 · 중앙 17건이다. n7 예산은 12건이므로 **측정된 12종목 전부**가
예산을 넘는다. packing 이 없으면 실호출 첫 종목에서 BUDGET_EXCEEDED 로 죽는다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.contexts.views import ClaimView, EvidenceExcerptView, EvidencePacket
from app.orchestration.evidence_packing import (
    fits_budget,
    order_evidence,
    pack_evidence,
)
from app.orchestration.nodes.s0 import make_nodes
from app.schemas.frozen import Claim, Evidence, EvidenceQueryLink, ProviderCall, Query, SourceTrace
from tests.s0.runtime_fixtures import NOW, deps, initial_state


def uid(n: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G{n:04d}"


def make_evidence(
    n: int,
    *,
    source_type: str = "news",
    minutes: int | None = 0,
    span: str = "짧은 근거",
) -> Evidence:
    return Evidence(
        evidence_id=uid(7000 + n),
        source_type=source_type,
        source_ref=f"ref-{n}",
        published_at=None if minutes is None else NOW + timedelta(minutes=minutes),
        fetched_at=NOW,
        raw_span=span,
        span_scope="headline_snippet",
        content_sha256=f"{n:064x}",
        provider_request_id=uid(6000 + n),
        as_of=NOW,
    )


def packet(items) -> EvidencePacket:
    return EvidencePacket(
        claim=ClaimView(claim_id=uid(9001), slot_id=4, normalized_proposition="주장"),
        evidence=[
            EvidenceExcerptView(**item.model_dump(include=set(EvidenceExcerptView.model_fields)))
            for item in items
        ],
    )


# ── 순서 ──────────────────────────────────────────────────────────


def test_source를_번갈아_고른다():
    """뉴스 30건이 12칸을 다 먹고 DART 재무가 통째로 빠지면 판정이 오염된다."""
    items = [make_evidence(i, source_type="news", minutes=-i) for i in range(1, 31)]
    items.append(make_evidence(99, source_type="dart", minutes=-999))

    ordered = order_evidence(items)

    assert ordered[0].source_type == "dart", "가장 희소한 source 가 먼저 자리를 잡는다"
    assert {item.source_type for item in ordered[:2]} == {"dart", "news"}


def test_같은_source_안에서는_최신이_먼저다():
    items = [
        make_evidence(1, minutes=-100),
        make_evidence(2, minutes=0),
        make_evidence(3, minutes=-50),
    ]
    assert [item.evidence_id for item in order_evidence(items)] == [
        uid(7002),
        uid(7003),
        uid(7001),
    ]


def test_published_at이_없으면_뒤로_보낸다():
    items = [make_evidence(1, minutes=None), make_evidence(2, minutes=-100)]
    assert [item.evidence_id for item in order_evidence(items)] == [uid(7002), uid(7001)]


def test_시각이_같으면_evidence_id로_결정한다():
    """골든셋 회귀가 성립하려면 같은 입력이 항상 같은 출력이어야 한다."""
    items = [make_evidence(3), make_evidence(1), make_evidence(2)]
    first = [item.evidence_id for item in order_evidence(items)]
    assert first == [uid(7001), uid(7002), uid(7003)]
    assert first == [item.evidence_id for item in order_evidence(list(reversed(items)))]


# ── 패킹 ──────────────────────────────────────────────────────────


def test_item_상한까지_자르고_자른_수를_돌려준다():
    items = [make_evidence(i) for i in range(1, 31)]
    kept, dropped = pack_evidence(items, item_limit=12, fits=lambda _: True)
    assert (len(kept), dropped) == (12, 18)


def test_문자_예산이_넘치면_더_줄인다():
    """item 수를 맞춰도 raw_span 500자 x 12건이면 n7 상한 4,000자를 넘는다."""
    items = [make_evidence(i, span="가" * 500) for i in range(1, 13)]
    kept, dropped = pack_evidence(
        items, item_limit=12, fits=lambda cand: fits_budget("n7", packet(cand))
    )
    assert len(kept) < 12
    assert dropped == 12 - len(kept)
    assert fits_budget("n7", packet(kept))


def test_한_건도_안_맞아도_한_건은_남긴다():
    """빈 packet 은 '근거 없음' 과 '근거를 못 실음' 을 구분 못 하게 만든다."""
    items = [make_evidence(i) for i in range(1, 5)]
    kept, dropped = pack_evidence(items, item_limit=4, fits=lambda _: False)
    assert (len(kept), dropped) == (1, 3)


def test_상한이_없으면_문자_예산만_본다():
    items = [make_evidence(i) for i in range(1, 6)]
    kept, dropped = pack_evidence(items, item_limit=None, fits=lambda _: True)
    assert (len(kept), dropped) == (5, 0)


def test_예산_안에_들면_그대로_둔다():
    items = [make_evidence(i) for i in range(1, 5)]
    kept, dropped = pack_evidence(
        items, item_limit=12, fits=lambda cand: fits_budget("n7", packet(cand))
    )
    assert (len(kept), dropped) == (4, 0)


@pytest.mark.parametrize("limit", [0, -1])
def test_상한이_양수가_아니면_거부한다(limit):
    with pytest.raises(ValueError):
        pack_evidence([], item_limit=limit, fits=lambda _: True)


def test_fits_budget은_item과_문자를_모두_본다():
    assert fits_budget("n7", packet([make_evidence(1)])) is True
    assert fits_budget("n7", packet([make_evidence(i) for i in range(1, 14)])) is False


# ── n7 통합 ───────────────────────────────────────────────────────


async def seed(runtime_deps, evidences: list[Evidence]) -> dict:
    text = "주장"
    item = Claim(
        claim_id=uid(9001),
        slot_id=4,
        user_text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=text,
        verifiable=True,
        origin=SourceTrace.SURVEY,
        created_at=NOW,
    )
    await runtime_deps.review_store.put_claims("run-s0", [item])
    query = Query(
        query_id=uid(8001),
        scope="claim",
        claim_id=item.claim_id,
        intent="verify",
        provider="naver",
        endpoint="news_search",
        params={"stock_code": "005930"},
        created_at=NOW,
    )
    await runtime_deps.evidence_store.put_queries("run-s0", [query])
    await runtime_deps.evidence_store.put_provider_calls(
        "run-s0",
        [
            ProviderCall(
                provider_request_id=evidence.provider_request_id,
                run_id="run-s0",
                provider="naver",
                endpoint="news_search",
                query_id=query.query_id,
                latency_ms=0,
                idempotency_key=f"{index:064x}",
                created_at=NOW,
            )
            for index, evidence in enumerate(evidences, 1)
        ],
    )
    await runtime_deps.evidence_store.put_evidence_batch(
        "run-s0",
        evidences,
        [
            EvidenceQueryLink(evidence_id=evidence.evidence_id, query_id=query.query_id)
            for evidence in evidences
        ],
    )
    return initial_state() | {
        "stock": {"code": "005930", "name": "삼성전자"},
        "claim_ids": [item.claim_id],
        "query_ids": [query.query_id],
    }


@pytest.mark.asyncio
async def test_n7은_뉴스_30건을_받아도_예산_안에서_돈다():
    """🔴 packing 이전에는 이 입력이 BUDGET_EXCEEDED 로 run 을 끝냈다.

    NAVER 는 쿼리당 display=30 이므로 이것이 예외가 아니라 기본값이다.
    """
    runtime_deps = deps()
    evidences = [
        make_evidence(i, source_type="news", minutes=-i, span=f"기사 제목 {i} " + "본문 " * 40)
        for i in range(1, 31)
    ]
    state = await seed(runtime_deps, evidences)

    patch = await make_nodes(runtime_deps)["n7"](state)

    links = await runtime_deps.review_store.get_claim_evidence("run-s0", uid(9001))
    assert patch["node_results"] == ["n7:partial"], "잘렸으면 partial 로 알려야 한다"
    assert 0 < len(links) <= 12
    assert len({item.evidence_id for item in links}) == len(links)


@pytest.mark.asyncio
async def test_n7은_예산_안에_들면_전부_분류하고_ok를_낸다():
    runtime_deps = deps()
    evidences = [make_evidence(i, source_type="news", minutes=-i) for i in range(1, 6)]
    state = await seed(runtime_deps, evidences)

    patch = await make_nodes(runtime_deps)["n7"](state)

    links = await runtime_deps.review_store.get_claim_evidence("run-s0", uid(9001))
    assert patch["node_results"] == ["n7:ok"]
    assert len(links) == 5
