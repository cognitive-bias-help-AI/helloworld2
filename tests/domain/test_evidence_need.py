from datetime import UTC, datetime

import pytest

from app.schemas.frozen import Claim, SourceTrace

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def claim(text: str, *, verifiable: bool = True, slot_id: int = 4) -> Claim:
    return Claim(
        claim_id="01ARZ3NDEKTSV4RRFFQ69G9100",
        slot_id=slot_id,
        user_text_span=text,
        span_offset=(0, len(text)),
        normalized_proposition=text,
        verifiable=verifiable,
        origin=SourceTrace.LLM_EXTRACTION,
        created_at=NOW,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025 사업보고서 연결 영업이익이 증가했다", "FINANCIAL_STATEMENT"),
        ("2025 사업보고서 수익성 PER이 낮아졌다", "FINANCIAL_INDICATOR"),
        ("유상증자 공시가 발표됐다", "DISCLOSURE"),
        ("최근 HBM 공급 확대 뉴스가 있다", "NEWS"),
        ("수정주가 기준 최근 주가가 20% 상승했다", "MARKET_PRICE"),
        ("외국인 순매수 수량 주 단위가 증가했다", "INVESTOR_FLOW"),
        ("HBM 전망이 좋다", "UNKNOWN"),
        ("2025 사업보고서 연결 영업이익 공시", "UNKNOWN"),
        ("2024 2025 사업보고서 연결 영업이익 증가", "UNKNOWN"),
        ("2025 사업보고서 연결 별도 영업이익 증가", "UNKNOWN"),
        ("외국인 순매수 수량 백만원 증가", "UNKNOWN"),
    ],
)
def test_classification은_필요한_명시적_신호만_허용한다(text, expected):
    from app.domain.evidence_need import classify_evidence_need

    assert classify_evidence_need(claim(text)).value == expected


def test_non_verifiable_claim은_UNKNOWN이다():
    from app.domain.evidence_need import EvidenceNeed, classify_evidence_need

    assert classify_evidence_need(claim("최근 뉴스", verifiable=False)) is EvidenceNeed.UNKNOWN
