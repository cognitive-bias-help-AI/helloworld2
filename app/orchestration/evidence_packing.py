"""Context 예산에 맞게 Evidence를 고르는 결정론적 패킹.

■ 왜 truncate() 호출만으로는 부족한가

`budget.truncate()` 는 "최오래 1건 + 최신 limit-1건" 이라는 한 가지 규칙이다.
그 규칙은 시간 축만 본다. 실제로 예산을 넘기는 상황은 이렇게 생겼다.

    NAVER 뉴스 30건 + DART 재무 1건  ->  n7 예산 12건

시간순으로 자르면 뉴스가 12칸을 다 먹고 **DART 재무 근거가 통째로 빠진다.**
재무 Claim 을 뉴스로만 판정하게 되는 것이라 이건 예산 문제가 아니라 판정
오염이다. 그래서 먼저 source 를 고르게 섞고, 그 다음에 시간을 본다.

■ 실측 근거 (검색모듈 골든셋 377건 / 12종목, 2026-08-20)

    귀속을 통과한 문서가 종목당 최소 13 · 중앙 17 · 최대 28건
    -> n7 예산 12건을 **측정된 12종목이 전부** 넘는다. 예외 상황이 아니다.

    문서의 41%(155/377)가 2개 이상 쿼리에 동시에 걸린다
    -> "쿼리별 균등 배분" 을 하면 같은 문서를 두 번 세게 된다. 쿼리 축 분산은
       문서 단위로 접은 뒤에 해야 의미가 있어서 이번 판에는 넣지 않았다.
       source 축만으로 위 오염은 막힌다.

■ 문자 예산

item 수를 맞춰도 문자 예산은 별개다. raw_span 이 최대 500자라 12건이면
6,000자가 되어 n7 상한(4,000자)을 넘는다. 그래서 **호출자가 넘긴 `fits` 로
실제 View 를 만들어 재고**, 안 맞으면 우선순위 낮은 것부터 하나씩 뺀다.
추정치로 계산하지 않는 이유는 View 마다 고정 오버헤드가 다르기 때문이다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from app.contexts.budget import NODE_BUDGETS, ctx_chars, ctx_items


class _Packable(Protocol):
    evidence_id: str
    source_type: str
    published_at: datetime | None



def fits_budget(node: str, view: BaseModel) -> bool:
    """`_budget()` 과 같은 판정을 예외 없이 돌려준다.

    노드의 `_budget()` 은 마지막 방어선으로 남긴다 — 패킹이 잘못돼도
    예산을 넘긴 View 가 모델로 나가지는 않아야 한다.
    """
    limit = NODE_BUDGETS[node]
    if limit.items is not None and ctx_items(view) > limit.items:
        return False
    return ctx_chars(view) <= limit.chars


def _recency_key(item: _Packable) -> tuple[int, float, str]:
    """최신 우선. published_at 이 없는 것은 뒤로 보내되 순서는 고정한다."""
    if item.published_at is None:
        return (1, 0.0, item.evidence_id)
    return (0, -item.published_at.timestamp(), item.evidence_id)


def order_evidence[EvidenceT: _Packable](items: Sequence[EvidenceT]) -> list[EvidenceT]:
    """source 를 번갈아 가며 최신 순으로 늘어놓는다.

    source_type 그룹 순서는 이름순으로 고정한다(dart < news < quote).
    같은 입력이면 항상 같은 출력이어야 골든셋 회귀가 성립한다.
    """
    groups: dict[str, list[EvidenceT]] = {}
    for item in items:
        groups.setdefault(item.source_type, []).append(item)
    for bucket in groups.values():
        bucket.sort(key=_recency_key)

    ordered: list[EvidenceT] = []
    names = sorted(groups)
    index = 0
    while len(ordered) < len(items):
        for name in names:
            bucket = groups[name]
            if index < len(bucket):
                ordered.append(bucket[index])
        index += 1
    return ordered


def pack_evidence[EvidenceT: _Packable](
    items: Sequence[EvidenceT],
    *,
    item_limit: int | None,
    fits: Callable[[Sequence[EvidenceT]], bool],
) -> tuple[list[EvidenceT], int]:
    """예산에 맞는 부분집합과 잘라낸 건수를 돌려준다.

    한 건도 안 맞으면 **한 건은 남긴다.** 빈 packet 을 모델에 보내면 n7/n8 이
    "근거가 없다" 와 "근거를 못 실었다" 를 구분할 수 없게 된다. 한 건이라도
    실어 보내고 잘림을 partial 로 알리는 편이 낫다.
    """
    if item_limit is not None and item_limit < 1:
        raise ValueError("item_limit must be positive")

    ordered = order_evidence(items)
    kept = ordered if item_limit is None else ordered[:item_limit]
    while len(kept) > 1 and not fits(kept):
        kept = kept[:-1]
    return list(kept), len(items) - len(kept)


__all__ = ["fits_budget", "order_evidence", "pack_evidence"]
