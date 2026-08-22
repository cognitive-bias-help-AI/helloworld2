"""Conservative, deterministic evidence-need classification for canonical Claims."""

from __future__ import annotations

import re
from enum import StrEnum

from app.schemas.frozen import Claim


class EvidenceNeed(StrEnum):
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"
    FINANCIAL_INDICATOR = "FINANCIAL_INDICATOR"
    DISCLOSURE = "DISCLOSURE"
    NEWS = "NEWS"
    MARKET_PRICE = "MARKET_PRICE"
    INVESTOR_FLOW = "INVESTOR_FLOW"
    UNKNOWN = "UNKNOWN"


_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_REPORT_TERMS = ("사업보고서", "반기보고서", "1분기", "3분기")
_ACCOUNT_TERMS = ("매출액", "영업이익", "당기순이익")
_INDICATOR_TERMS = ("PER", "PBR", "ROE", "ROA", "부채비율", "회전율")
_INDICATOR_FAMILIES = ("수익성", "안정성", "성장성", "활동성")


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def _exactly_one(text: str, values: tuple[str, ...]) -> bool:
    return sum(value in text for value in values) == 1


def classify_evidence_need(claim: Claim) -> EvidenceNeed:
    """Return UNKNOWN unless exactly one fully specified evidence shape matches."""

    if not claim.verifiable:
        return EvidenceNeed.UNKNOWN
    text = claim.normalized_proposition
    has_period = (
        len(set(_YEAR.findall(text))) == 1 and _exactly_one(text, _REPORT_TERMS)
    )
    flow_measure = (
        "quantity"
        if _exactly_one(text, ("수량", "금액")) and "수량" in text
        else "amount"
        if _exactly_one(text, ("수량", "금액"))
        else None
    )
    flow_unit = (
        "shares"
        if _exactly_one(text, ("주 단위", "천주", "백만원")) and "주 단위" in text
        else "thousand_shares"
        if _exactly_one(text, ("주 단위", "천주", "백만원")) and "천주" in text
        else "million_krw"
        if _exactly_one(text, ("주 단위", "천주", "백만원"))
        else None
    )
    flow_unit_valid = (flow_measure, flow_unit) in {
        ("quantity", "shares"),
        ("quantity", "thousand_shares"),
        ("amount", "million_krw"),
    }
    flow_trade_valid = (
        "순매수" in text and "매도" not in text
    ) or ("순매수" not in text and _exactly_one(text, ("매수", "매도")))
    candidates = {
        EvidenceNeed.FINANCIAL_STATEMENT: (
            has_period
            and _exactly_one(text, ("연결", "별도"))
            and _contains_any(text, _ACCOUNT_TERMS)
        ),
        EvidenceNeed.FINANCIAL_INDICATOR: (
            has_period
            and _exactly_one(text, _INDICATOR_FAMILIES)
            and _contains_any(text, _INDICATOR_TERMS)
        ),
        EvidenceNeed.DISCLOSURE: "공시" in text,
        EvidenceNeed.NEWS: _contains_any(text, ("뉴스", "보도", "기사")),
        EvidenceNeed.MARKET_PRICE: (
            _contains_any(text, ("주가", "종가"))
            and _contains_any(text, ("수정주가", "비수정주가"))
        ),
        EvidenceNeed.INVESTOR_FLOW: (
            _contains_any(text, ("외국인", "기관"))
            and flow_trade_valid
            and flow_unit_valid
        ),
    }
    matched = [need for need, applies in candidates.items() if applies]
    return matched[0] if len(matched) == 1 else EvidenceNeed.UNKNOWN


__all__ = ["EvidenceNeed", "classify_evidence_need"]
