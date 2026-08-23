"""Claim이 **어떤 종류의 근거를 필요로 하는가**만 판정한다.

🔴 B1 — 이 파일은 더 이상 파라미터 충분성을 보지 않는다.

이전 판은 두 가지를 한꺼번에 물었다.

    "이 Claim에 무슨 근거가 필요한가?"
    "Provider API를 호출할 만큼 구체적인가?"

그 결과 FINANCIAL_STATEMENT가 되려면 연도 + 보고서종류 + 연결/별도 + 계정명이
문장에 **전부 리터럴로** 있어야 했다. 그래서

    "삼성전자 영업이익이 증가했다"

같은 자연스러운 문장이 UNKNOWN이 됐고, 테스트는 Mock이 문장을
"2025 사업보고서 연결 영업이익이 증가했다"로 바꿔줬기 때문에 통과했다.
사용자가 말한 적 없는 연도·보고서·연결기준을 모델이 채우는 구조였고,
그건 이 시스템의 fail-closed 원칙과 정면으로 충돌한다.

이제 나눈다.

    1. 여기(EvidenceNeed)        무슨 종류의 근거인가          ← 의미
    2. evidence_planning         호출에 필요한 값이 충분한가    ← 파라미터

■ 여전히 fail-closed다

신호가 없으면 UNKNOWN이다. 그리고 **서로 다른 need 신호가 둘 이상이면
UNKNOWN이다** — "영업이익 공시" 처럼 재무제표와 공시를 동시에 가리키는 문장은
어느 쪽으로 조회해야 하는지 알 수 없다. 추측해서 한쪽을 고르지 않는다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from app.schemas.frozen import Claim


class EvidenceNeed(StrEnum):
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"
    FINANCIAL_INDICATOR = "FINANCIAL_INDICATOR"
    DISCLOSURE = "DISCLOSURE"
    NEWS = "NEWS"
    MARKET_PRICE = "MARKET_PRICE"
    INVESTOR_FLOW = "INVESTOR_FLOW"
    UNKNOWN = "UNKNOWN"


# ── 의미 신호 ─────────────────────────────────────────────────────
#
# 계정명·지표명은 파라미터이기도 하지만 **무슨 종류의 근거인지를 결정하는
# 신호**이기도 하다. 여기서는 신호로만 쓰고, 값으로 쓰는 것은
# evidence_planning 의 일이다.

ACCOUNT_TERMS: Final[tuple[str, ...]] = ("매출액", "영업이익", "당기순이익")
# "실적" 은 계정을 특정하지 않지만 재무제표를 가리키는 것은 분명하다.
# need = FINANCIAL_STATEMENT + missing = account_names 로 남는 편이
# UNKNOWN 으로 뭉개는 것보다 보고서에 쓸 말이 많다.
_STATEMENT_TERMS: Final[tuple[str, ...]] = ("재무제표", "손익계산서", "재무상태표", "실적")

INDICATOR_TERMS: Final[tuple[str, ...]] = ("PER", "PBR", "ROE", "ROA", "부채비율", "회전율")
INDICATOR_FAMILIES: Final[dict[str, str]] = {
    "수익성": "profitability",
    "안정성": "stability",
    "성장성": "growth",
    "활동성": "activity",
}

_DISCLOSURE_TERMS: Final[tuple[str, ...]] = ("공시",)
_NEWS_TERMS: Final[tuple[str, ...]] = ("뉴스", "보도", "기사")
_PRICE_TERMS: Final[tuple[str, ...]] = ("주가", "종가", "시세")
_FLOW_ACTOR_TERMS: Final[tuple[str, ...]] = ("외국인", "기관")
TRADE_TERMS: Final[tuple[str, ...]] = ("순매수", "매수", "매도")


def contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def _signals(text: str) -> dict[EvidenceNeed, bool]:
    return {
        EvidenceNeed.FINANCIAL_STATEMENT: contains_any(text, ACCOUNT_TERMS)
        or contains_any(text, _STATEMENT_TERMS),
        EvidenceNeed.FINANCIAL_INDICATOR: contains_any(text, INDICATOR_TERMS)
        or contains_any(text, tuple(INDICATOR_FAMILIES)),
        EvidenceNeed.DISCLOSURE: contains_any(text, _DISCLOSURE_TERMS),
        EvidenceNeed.NEWS: contains_any(text, _NEWS_TERMS),
        EvidenceNeed.MARKET_PRICE: contains_any(text, _PRICE_TERMS),
        EvidenceNeed.INVESTOR_FLOW: contains_any(text, _FLOW_ACTOR_TERMS)
        and contains_any(text, TRADE_TERMS),
    }


def classify_evidence_need(claim: Claim) -> EvidenceNeed:
    """정확히 하나의 근거 종류가 지목될 때만 그것을 돌려준다.

    파라미터가 충분한지는 보지 않는다. 연도가 없어도
    "영업이익이 증가했다" 는 FINANCIAL_STATEMENT 다 — 무슨 근거가 필요한지는
    분명하기 때문이다. 조회 가능한지는 evidence_planning 이 따로 답한다.
    """
    if not claim.verifiable:
        return EvidenceNeed.UNKNOWN
    matched = [need for need, applies in _signals(claim.normalized_proposition).items() if applies]
    return matched[0] if len(matched) == 1 else EvidenceNeed.UNKNOWN


__all__ = [
    "ACCOUNT_TERMS",
    "INDICATOR_FAMILIES",
    "INDICATOR_TERMS",
    "TRADE_TERMS",
    "EvidenceNeed",
    "classify_evidence_need",
    "contains_any",
]
