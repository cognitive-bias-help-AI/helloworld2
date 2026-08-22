"""Translate a classified EvidenceNeed into frozen Query objects."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from app.domain.evidence_need import EvidenceNeed, classify_evidence_need
from app.domain.slots import EvidencePolicy, get_slot_definition
from app.schemas.frozen import Claim, Query
from providers.naver.query import build_query_params

_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_REPORT_CODES = {
    "사업보고서": "11011",
    "반기보고서": "11012",
    "1분기": "11013",
    "3분기": "11014",
}
_ACCOUNT_NAMES = ("매출액", "영업이익", "당기순이익")
_INDICATOR_FAMILIES = {
    "수익성": "profitability",
    "안정성": "stability",
    "성장성": "growth",
    "활동성": "activity",
}


def _single_match(text: str, values: dict[str, str]) -> str:
    matched = [mapped for token, mapped in values.items() if token in text]
    if len(matched) != 1:
        raise ValueError("evidence planning requires exactly one explicit parameter")
    return matched[0]


def _year(text: str) -> str:
    values = _YEAR.findall(text)
    if len(set(values)) != 1:
        raise ValueError("evidence planning requires exactly one business year")
    return values[0]


def _provider_params(
    need: EvidenceNeed,
    text: str,
    *,
    stock_code: str,
    stock_name: str,
    as_of: datetime,
) -> tuple[tuple[str, str, dict[str, object]], ...]:
    if need is EvidenceNeed.FINANCIAL_STATEMENT:
        accounts = [name for name in _ACCOUNT_NAMES if name in text]
        return ((
            "dart",
            "financial_statement",
            {
                "stock_code": stock_code,
                "bsns_year": _year(text),
                "reprt_code": _single_match(text, _REPORT_CODES),
                "fs_div": _single_match(text, {"연결": "CFS", "별도": "OFS"}),
                "account_names": accounts,
            },
        ),)
    if need is EvidenceNeed.FINANCIAL_INDICATOR:
        return ((
            "dart",
            "financial_indicator",
            {
                "stock_code": stock_code,
                "bsns_year": _year(text),
                "reprt_code": _single_match(text, _REPORT_CODES),
                "indicator_family": _single_match(text, _INDICATOR_FAMILIES),
            },
        ),)
    if need is EvidenceNeed.DISCLOSURE:
        return (("dart", "disclosure_list", {"stock_code": stock_code}),)
    if need is EvidenceNeed.NEWS:
        return tuple(
            ("naver", "news_search", params)
            for params in build_query_params(stock_code, stock_name)
        )
    if need is EvidenceNeed.MARKET_PRICE:
        return ((
            "kiwoom",
            "daily_price_history",
            {
                "stock_code": stock_code,
                "base_date": as_of.strftime("%Y%m%d"),
                "adjusted_price": "비수정주가" not in text,
            },
        ),)
    if need is EvidenceNeed.INVESTOR_FLOW:
        measure = _single_match(text, {"수량": "quantity", "금액": "amount"})
        trade_kind = _single_match(
            text.replace("순매수", ""),
            {"매수": "buy", "매도": "sell"},
        ) if "순매수" not in text else "net_buy"
        unit = _single_match(
            text,
            {"주 단위": "shares", "천주": "thousand_shares", "백만원": "million_krw"},
        )
        return ((
            "kiwoom",
            "investor_flow",
            {
                "stock_code": stock_code,
                "date": as_of.strftime("%Y%m%d"),
                "measure": measure,
                "trade_kind": trade_kind,
                "unit": unit,
            },
        ),)
    return ()


def plan_claim_queries(
    claim: Claim,
    *,
    stock_code: str,
    stock_name: str,
    as_of: datetime,
    id_factory: Callable[[], str],
    clock: Callable[[], datetime],
) -> tuple[Query, ...]:
    need = classify_evidence_need(claim)
    if need is EvidenceNeed.UNKNOWN:
        return ()
    policy = get_slot_definition(claim.slot_id).evidence_policy
    intent = "counter" if policy is EvidencePolicy.SYSTEM_OPPOSING_SEARCH else "verify"
    try:
        planned = _provider_params(
            need,
            claim.normalized_proposition,
            stock_code=stock_code,
            stock_name=stock_name,
            as_of=as_of,
        )
    except ValueError:
        return ()
    return tuple(
        Query(
            query_id=id_factory(),
            scope="claim",
            claim_id=claim.claim_id,
            intent=intent,
            provider=provider,
            endpoint=endpoint,
            params=params,
            created_at=clock(),
        )
        for provider, endpoint, params in planned
    )


__all__ = ["plan_claim_queries"]
