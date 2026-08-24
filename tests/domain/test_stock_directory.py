"""CsvStockDirectory 계약."""

import pytest

from app.domain.protocols import StockResolver
from app.domain.stock_directory import (
    CsvStockDirectory,
    chosung_of,
    load_rows,
    normalize,
)
from app.domain.stock_scope import AssetType, evaluate_stock_scope

HEADER = "code,name,market,asset_type,aliases,is_delisted,is_managed\n"
ROWS = (
    "005930,삼성전자,KOSPI,COMMON_STOCK,삼전,0,0\n"
    "005935,삼성전자우,KOSPI,PREFERRED_STOCK,삼전우,0,0\n"
    "000660,SK하이닉스,KOSPI,COMMON_STOCK,하이닉스,0,0\n"
    "247540,에코프로비엠,KOSDAQ,COMMON_STOCK,,0,0\n"
    "086520,에코프로,KOSDAQ,COMMON_STOCK,,0,0\n"
)


def directory(tmp_path, body: str = HEADER + ROWS) -> CsvStockDirectory:
    path = tmp_path / "stocks.csv"
    path.write_text(body, encoding="utf-8")
    return CsvStockDirectory.from_csv(path)


def test_StockResolver_Protocol을_만족한다(tmp_path):
    assert isinstance(directory(tmp_path), StockResolver)


# ── 매칭 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "code", "kind"),
    [
        ("005930", "005930", "exact_code"),
        ("삼성전자", "005930", "exact_name"),
        ("삼전", "005930", "alias"),
        ("ㅅㅅㅈㅈ", "005930", "chosung"),
        ("삼성전자 살까? HBM 전망이 좋아 보여", "005930", "exact_name"),
        ("하이닉스 어때", "000660", "alias"),
    ],
)
def test_매칭_종류별로_해석한다(tmp_path, query, code, kind):
    got = directory(tmp_path).resolve(query)
    assert got[0].code == code
    assert got[0].match_kind == kind


def test_긴_이름이_짧은_이름을_먹지_않는다(tmp_path):
    """🔴 '삼성전자우'는 '삼성전자'를 함께 반환하면 안 된다.

    긴 이름을 먼저 찾고 그 자리를 가리지 않으면 부분 문자열이 그대로 걸린다.
    사용자가 우선주를 지목했는데 보통주가 후보로 끼어들면 n2 가 불필요한
    HITL 을 띄운다.
    """
    got = directory(tmp_path).resolve("삼성전자우")
    assert [item.code for item in got] == ["005935"]


def test_접두어가_겹치는_종목도_같은_규칙을_따른다(tmp_path):
    got = directory(tmp_path).resolve("에코프로비엠 어떻게 생각해")
    assert [item.code for item in got] == ["247540"]


def test_둘_다_실제로_언급되면_둘_다_반환한다(tmp_path):
    """가리기는 '한 번 쓰인 자리'만 가린다. 진짜 두 종목 언급은 살아 있어야 한다."""
    got = directory(tmp_path).resolve("에코프로랑 에코프로비엠 둘 다 봤어")
    assert {item.code for item in got} == {"086520", "247540"}
    assert got[0].code == "247540", "더 구체적으로 맞은 쪽이 앞에 온다"


def test_모르는_종목은_빈_목록이다(tmp_path):
    assert directory(tmp_path).resolve("존재하지않는회사") == []


def test_limit을_넘겨_반환하지_않는다(tmp_path):
    assert len(directory(tmp_path).resolve("에코프로랑 에코프로비엠", limit=1)) == 1


def test_limit이_0이하면_거부한다(tmp_path):
    with pytest.raises(ValueError):
        directory(tmp_path).resolve("삼성전자", limit=0)


# ── resolve_exact ─────────────────────────────────────────────────


def test_resolve_exact는_코드_완전일치만_본다(tmp_path):
    resolver = directory(tmp_path)
    assert [item.name for item in resolver.resolve_exact("005935")] == ["삼성전자우"]
    assert resolver.resolve_exact("999999") == []


def test_resolve_exact가_asset_type을_보존한다(tmp_path):
    """n2 가 evaluate_stock_scope 로 지원 여부를 판정하므로 이 값이 살아야 한다."""
    (instrument,) = directory(tmp_path).resolve_exact("005935")
    assert instrument.asset_type is AssetType.PREFERRED_STOCK
    assert evaluate_stock_scope(instrument).supported is True


def test_CSV_resolver는_다섯번째_영문코드를_직접_해석한다(tmp_path):
    resolver = directory(
        tmp_path,
        HEADER + "0126Z0,삼성에피스홀딩스,KOSPI,COMMON_STOCK,,0,0\n",
    )
    assert resolver.resolve_exact("0126Z0")[0].name == "삼성에피스홀딩스"
    assert resolver.resolve("0126Z0 살까?")[0].match_kind == "exact_code"


# ── CSV 검증 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "row",
    [
        "5930,삼성전자,KOSPI,COMMON_STOCK,,0,0\n",          # 코드 자릿수
        "005930,,KOSPI,COMMON_STOCK,,0,0\n",                 # 종목명 없음
        "005930,삼성전자,NASDAQ,COMMON_STOCK,,0,0\n",        # 시장 구분
        "005930,삼성전자,KOSPI,BOND,,0,0\n",                 # asset_type
    ],
)
def test_형식이_틀린_행은_조용히_건너뛰지_않는다(tmp_path, row):
    """한 줄을 건너뛰면 그 종목만 '없는 종목'이 되고 원인을 데이터에서 못 찾는다."""
    with pytest.raises(ValueError):
        directory(tmp_path, HEADER + row)


def test_코드가_중복되면_거부한다(tmp_path):
    body = HEADER + "005930,삼성전자,KOSPI,COMMON_STOCK,,0,0\n" * 2
    with pytest.raises(ValueError):
        directory(tmp_path, body)


def test_비어_있는_파일을_거부한다(tmp_path):
    with pytest.raises(ValueError):
        directory(tmp_path, HEADER)


def test_파일이_없으면_경로를_알려준다(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_rows(tmp_path / "없다.csv")


def test_상장폐지_플래그가_전달된다(tmp_path):
    body = HEADER + "005930,삼성전자,KOSPI,COMMON_STOCK,,1,0\n"
    (instrument,) = directory(tmp_path, body).resolve_exact("005930")
    assert instrument.is_delisted is True
    assert evaluate_stock_scope(instrument).supported is False


# ── 정규화 헬퍼 ───────────────────────────────────────────────────


def test_normalize는_공백과_대소문자를_지운다():
    assert normalize(" SK 하이닉스 ") == "SK하이닉스"
    assert normalize("naver") == "NAVER"


def test_chosung은_한글만_초성으로_바꾼다():
    assert chosung_of("삼성전자") == "ㅅㅅㅈㅈ"
    assert chosung_of("LG화학") == "LGㅎㅎ"


def test_배포된_seed_파일이_유효하다():
    """data/stock_directory.csv 가 깨지면 데모가 n2 에서 멈춘다."""
    resolver = CsvStockDirectory.from_csv("data/stock_directory.csv")
    assert resolver.resolve("삼성전자")[0].code == "005930"
