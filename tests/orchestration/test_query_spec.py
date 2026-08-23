"""Query Specification 계약 (B1) — 사용자 사실과 조회 정책의 경계.

이 파일이 잠그는 것은 하나다:

    없으면 조회하지 않는 값(사용자 사실)과
    시스템이 정해도 되는 값(조회 정책)을 **어디서 갈랐는가**

이 경계가 흔들리면 둘 중 하나가 된다. 너무 좁으면 자연스러운 문장이 전부
UNKNOWN 이 되고(B1 이전), 너무 넓으면 사용자가 하지 않은 주장을 검증한다.
"""

from datetime import UTC, datetime

import pytest

from app.domain.evidence_need import EvidenceNeed
from app.orchestration.evidence_planning import (
    POLICY_FS_DIV,
    POLICY_REPORT_CODE,
    missing_parameters,
    resolve_parameters,
)

AS_OF = datetime(2026, 8, 23, tzinfo=UTC)


def resolve(need: EvidenceNeed, text: str):
    return resolve_parameters(
        need, text, stock_code="005930", stock_name="삼성전자", as_of=AS_OF
    )


def params_of(resolution):
    assert resolution.ready, f"조회 가능해야 한다. missing={resolution.missing}"
    (_, _, params), = resolution.planned
    return params


# ── 사용자 사실: 없으면 조회하지 않는다 ───────────────────────────


def test_사업연도가_없으면_조회하지_않는다():
    """🔴 연도는 시스템이 고르면 안 된다.

    "2024년 영업이익" 과 "2025년 영업이익" 은 서로 다른 주장이다.
    시스템이 하나를 고르면 사용자가 하지 않은 주장을 검증하게 된다.
    """
    resolution = resolve(EvidenceNeed.FINANCIAL_STATEMENT, "영업이익이 증가했다")
    assert resolution.missing == ("bsns_year",)
    assert resolution.planned == ()


def test_사업연도가_둘이면_조회하지_않는다():
    resolution = resolve(EvidenceNeed.FINANCIAL_STATEMENT, "2024 2025 영업이익 증가")
    assert "bsns_year" in resolution.missing


def test_계정을_특정하지_못하면_조회하지_않는다():
    """'실적' 은 재무제표를 가리키지만 어느 계정인지는 말하지 않았다."""
    resolution = resolve(EvidenceNeed.FINANCIAL_STATEMENT, "2024 실적이 개선됐다")
    assert resolution.missing == ("account_names",)


def test_매수인지_매도인지는_사용자_사실이다():
    resolution = resolve(EvidenceNeed.INVESTOR_FLOW, "외국인 비중이 높다")
    assert resolution.missing == ("trade_kind",)


# ── 조회 정책: 시스템이 정한다 ────────────────────────────────────


def test_보고서_종류와_연결기준은_정책으로_채운다():
    """연도만 있으면 조회할 수 있어야 한다.

    보고서 종류(사업보고서)와 연결/별도는 무엇을 검증할지가 아니라
    어떻게 가져올지의 문제다. 매번 되물으면 되묻기 예산만 쓴다.
    """
    resolution = resolve(EvidenceNeed.FINANCIAL_STATEMENT, "2024 영업이익이 증가했다")

    assert params_of(resolution) == {
        "stock_code": "005930",
        "bsns_year": "2024",
        "reprt_code": POLICY_REPORT_CODE,
        "fs_div": POLICY_FS_DIV,
        "account_names": ["영업이익"],
    }
    assert set(resolution.policy_applied) == {"reprt_code", "fs_div"}


def test_사용자가_말하면_정책보다_사용자를_따른다():
    resolution = resolve(
        EvidenceNeed.FINANCIAL_STATEMENT, "2024 반기보고서 별도 영업이익이 증가했다"
    )
    params = params_of(resolution)
    assert (params["reprt_code"], params["fs_div"]) == ("11012", "OFS")
    assert resolution.policy_applied == ()


def test_수정주가_여부는_정책이다():
    """'수정주가 기준인가요?' 를 되묻는 것은 판단을 바꾸지 못한다."""
    resolution = resolve(EvidenceNeed.MARKET_PRICE, "최근 주가가 많이 올랐다")
    params = params_of(resolution)
    assert params["adjusted_price"] is True
    assert params["base_date"] == "20260823"
    assert set(resolution.policy_applied) == {"base_date", "adjusted_price"}


def test_비수정주가를_말하면_그것을_따른다():
    resolution = resolve(EvidenceNeed.MARKET_PRICE, "비수정주가 기준 주가가 올랐다")
    assert params_of(resolution)["adjusted_price"] is False


def test_수급_단위는_정책_기본값이_있다():
    resolution = resolve(EvidenceNeed.INVESTOR_FLOW, "외국인이 순매수하고 있다")
    params = params_of(resolution)
    assert (params["measure"], params["unit"], params["trade_kind"]) == (
        "amount",
        "million_krw",
        "net_buy",
    )
    assert set(resolution.policy_applied) == {"date", "measure", "unit"}


def test_단위를_말하면_measure가_따라온다():
    """'천주' 라고 했으면 수량 기준인 것이 자명하다. 되물을 필요가 없다."""
    resolution = resolve(EvidenceNeed.INVESTOR_FLOW, "외국인 매수 천주 증가")
    params = params_of(resolution)
    assert (params["measure"], params["unit"]) == ("quantity", "thousand_shares")


# ── 모호하면 조회하지 않는다 ──────────────────────────────────────


@pytest.mark.parametrize(
    ("need", "text", "expected"),
    [
        (EvidenceNeed.FINANCIAL_STATEMENT, "2024 연결 별도 영업이익 증가", "fs_div"),
        (
            EvidenceNeed.FINANCIAL_STATEMENT,
            "2024 사업보고서 반기보고서 영업이익 증가",
            "reprt_code",
        ),
        (EvidenceNeed.INVESTOR_FLOW, "외국인 순매수 수량 백만원 증가", "unit"),
        (EvidenceNeed.INVESTOR_FLOW, "외국인 매수 매도 증가", "trade_kind"),
        (EvidenceNeed.FINANCIAL_INDICATOR, "2024 PER 이 낮아졌다", "indicator_family"),
    ],
)
def test_모호한_값은_정책으로_메우지_않는다(need, text, expected):
    """정책은 '사용자가 말하지 않았을 때' 쓰는 것이지 '말했는데 모를 때' 쓰는 게 아니다."""
    resolution = resolve(need, text)
    assert expected in resolution.missing
    assert resolution.planned == ()


def test_지표는_지표명에서_family를_끌어낸다():
    resolution = resolve(EvidenceNeed.FINANCIAL_INDICATOR, "2024 부채비율이 낮아졌다")
    assert params_of(resolution)["indicator_family"] == "stability"


# ── 파라미터가 필요 없는 need ─────────────────────────────────────


@pytest.mark.parametrize(
    ("need", "provider"),
    [(EvidenceNeed.DISCLOSURE, "dart"), (EvidenceNeed.NEWS, "naver")],
)
def test_종목만으로_조회되는_need는_항상_준비된다(need, provider):
    resolution = resolve(need, "공시가 나왔고 뉴스도 있다")
    assert resolution.ready
    assert {item[0] for item in resolution.planned} == {provider}
    assert resolution.missing == ()


# ── n9 가 쓰는 경로 ───────────────────────────────────────────────


def test_missing_parameters는_종목_정보와_무관하다():
    """n9 는 stock 을 안 들고 있어도 '계획 안 한 게 맞는가' 를 판정해야 한다."""
    assert missing_parameters(EvidenceNeed.FINANCIAL_STATEMENT, "영업이익이 증가했다") == (
        "bsns_year",
    )
    assert missing_parameters(EvidenceNeed.FINANCIAL_STATEMENT, "2024 영업이익 증가") == ()
    assert missing_parameters(EvidenceNeed.NEWS, "뉴스가 있다") == ()


def test_UNKNOWN은_부족한_파라미터를_말하지_않는다():
    """무슨 근거가 필요한지 모르는 것과 값이 부족한 것은 다른 상태다."""
    assert missing_parameters(EvidenceNeed.UNKNOWN, "HBM 전망이 좋다") == ()
