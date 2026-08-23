"""EvidenceNeed 계약 — **의미만** 판정한다 (B1).

이전 계약은 "파라미터가 충분한가" 까지 여기서 물었다. 그래서 연도·보고서종류·
연결기준이 문장에 리터럴로 없으면 UNKNOWN 이었고, 자연스러운 사용자 문장이
전부 걸러졌다. 파라미터 충분성은 evidence_planning 으로 옮겼다
(tests/orchestration/test_evidence_planning.py).
"""

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
        # 파라미터가 완전한 문장 — 이전에도 통과했다
        ("2025 사업보고서 연결 영업이익이 증가했다", "FINANCIAL_STATEMENT"),
        ("2025 사업보고서 수익성 PER이 낮아졌다", "FINANCIAL_INDICATOR"),
        ("유상증자 공시가 발표됐다", "DISCLOSURE"),
        ("최근 HBM 공급 확대 뉴스가 있다", "NEWS"),
        ("수정주가 기준 최근 주가가 20% 상승했다", "MARKET_PRICE"),
        ("외국인 순매수 수량 주 단위가 증가했다", "INVESTOR_FLOW"),
        # 🔴 B1 — 파라미터가 없어도 **무슨 근거가 필요한지는 분명하다**.
        #    이 여섯 줄이 이전에는 전부 UNKNOWN 이었다.
        ("삼성전자 영업이익이 증가했다", "FINANCIAL_STATEMENT"),
        ("실적이 개선되고 있다", "FINANCIAL_STATEMENT"),
        ("부채비율이 낮아졌다", "FINANCIAL_INDICATOR"),
        ("최근 주가가 많이 올랐다", "MARKET_PRICE"),
        # "사고 있다" -> "순매수하고 있다" 같은 표현 정리는 n3 정규화의 몫이다.
        # 여기서 동사 변형을 추론하기 시작하면 오탐이 조용히 늘어난다.
        ("외국인이 순매수하고 있다", "INVESTOR_FLOW"),
        ("2024 2025 사업보고서 연결 영업이익 증가", "FINANCIAL_STATEMENT"),
        # 신호가 없으면 여전히 UNKNOWN 이다
        ("HBM 전망이 좋다", "UNKNOWN"),
        ("그냥 마음에 든다", "UNKNOWN"),
        # 서로 다른 근거 종류를 동시에 가리키면 UNKNOWN — 추측해서 고르지 않는다
        ("2025 사업보고서 연결 영업이익 공시", "UNKNOWN"),
        ("영업이익 관련 뉴스가 나왔다", "UNKNOWN"),
    ],
)
def test_의미_신호로만_분류한다(text, expected):
    from app.domain.evidence_need import classify_evidence_need

    assert classify_evidence_need(claim(text)).value == expected


def test_non_verifiable_claim은_UNKNOWN이다():
    from app.domain.evidence_need import EvidenceNeed, classify_evidence_need

    assert classify_evidence_need(claim("최근 뉴스", verifiable=False)) is EvidenceNeed.UNKNOWN


def test_파라미터_부족은_UNKNOWN이_아니다():
    """분류 단계는 "조회 가능한가" 를 묻지 않는다.

    이 둘을 섞으면 '무슨 근거가 필요한지 모른다' 와 '알지만 값이 부족하다' 를
    구분할 수 없고, 보고서가 후자를 전자처럼 설명하게 된다.
    """
    from app.domain.evidence_need import EvidenceNeed, classify_evidence_need
    from app.orchestration.evidence_planning import missing_parameters

    need = classify_evidence_need(claim("삼성전자 영업이익이 증가했다"))

    assert need is EvidenceNeed.FINANCIAL_STATEMENT
    assert missing_parameters(need, "삼성전자 영업이익이 증가했다") == ("bsns_year",)
