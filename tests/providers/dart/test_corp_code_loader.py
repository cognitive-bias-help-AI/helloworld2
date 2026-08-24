"""OpenDART corpCode 로더 계약."""

import io
import json
import zipfile

import pytest

from providers.dart.corp_code import DartCorpCodeResolver
from providers.dart.corp_code_loader import (
    CorpCodeUnavailable,
    fetch_corp_code_mapping,
    load_mapping,
    parse_corp_code_xml,
    parse_corp_code_zip,
    save_mapping,
)


def entry(corp_code: str, name: str, stock_code: str) -> str:
    return (
        f"<list><corp_code>{corp_code}</corp_code>"
        f"<corp_name>{name}</corp_name>"
        f"<stock_code>{stock_code}</stock_code>"
        f"<modify_date>20240101</modify_date></list>"
    )


XML = (
    '<?xml version="1.0" encoding="UTF-8"?><result>'
    + entry("00126380", "삼성전자", "005930")
    + entry("00126380", "삼성전자우", "005935")
    + entry("00164779", "SK하이닉스", "000660")
    + entry("01965324", "삼성에피스홀딩스", "0126Z0")
    + entry("00999999", "비상장회사", " ")
    + entry("bad", "corp_code 형식오류", "111111")
    + entry("00888888", "stock_code 형식오류", "12345")
    + "</result>"
)


def zipped(xml: str, member: str = "CORPCODE.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, xml.encode("utf-8"))
    return buffer.getvalue()


# ── XML 파싱 ──────────────────────────────────────────────────────


def test_상장_종목만_남긴다():
    assert parse_corp_code_xml(XML) == {
        "005930": "00126380",
        "005935": "00126380",
        "000660": "00164779",
        "0126Z0": "01965324",
    }


def test_우선주는_보통주와_같은_발행법인을_가리킨다():
    """Security(005930·005935) 는 둘이지만 Issuer 는 하나다.

    DART 는 Issuer 단위이므로 이게 정상이다. 우선주 코드를 받아도 재무제표를
    가져올 수 있다는 뜻이고, 그래서 로더가 우선주를 버리면 안 된다.
    """
    mapping = parse_corp_code_xml(XML)
    assert mapping["005930"] == mapping["005935"]


def test_형식이_틀린_항목은_건너뛴다():
    """상장 3천 건 중 1건이 이상하다고 전체 매핑이 죽으면 안 된다."""
    mapping = parse_corp_code_xml(XML)
    assert "111111" not in mapping
    assert "12345" not in mapping


def test_상장_종목이_하나도_없으면_실패로_본다():
    """개별 항목 누락과 달리, 전부 비면 응답 형식이 바뀐 것이다."""
    empty = '<?xml version="1.0"?><result>' + entry("00999999", "비상장", "") + "</result>"
    with pytest.raises(CorpCodeUnavailable):
        parse_corp_code_xml(empty)


def test_XML이_깨지면_실패한다():
    with pytest.raises(CorpCodeUnavailable):
        parse_corp_code_xml("<result><list>")


# ── ZIP ───────────────────────────────────────────────────────────


def test_ZIP에서_XML을_꺼낸다():
    assert parse_corp_code_zip(zipped(XML)) == parse_corp_code_xml(XML)


def test_멤버_이름의_대소문자를_가리지_않는다():
    assert parse_corp_code_zip(zipped(XML, "CORPCODE.XML")) == parse_corp_code_xml(XML)


def test_ZIP이_아니면_인증키를_의심하라고_알린다():
    """인증키가 틀리면 OpenDART 는 ZIP 대신 에러 XML 을 보낸다.

    'BadZipFile' 만 뜨면 원인을 찾는 데 시간이 걸린다.
    """
    body = '<result><status>013</status><message>인증키가 유효하지 않습니다</message></result>'
    with pytest.raises(CorpCodeUnavailable, match="인증키"):
        parse_corp_code_zip(body.encode("utf-8"))


def test_ZIP에_CORPCODE가_없으면_멤버를_보여준다():
    with pytest.raises(CorpCodeUnavailable, match="OTHER.xml"):
        parse_corp_code_zip(zipped(XML, "OTHER.xml"))


# ── 캐시 저장/적재 ────────────────────────────────────────────────


def test_저장한_매핑을_그대로_읽는다(tmp_path):
    mapping = parse_corp_code_xml(XML)
    path = save_mapping(tmp_path / "nested" / "corp.json", mapping)
    assert load_mapping(path) == mapping


def test_캐시가_없으면_받는_방법을_알려준다(tmp_path):
    with pytest.raises(CorpCodeUnavailable, match="corp-code"):
        load_mapping(tmp_path / "없다.json")


def test_깨진_캐시를_거부한다(tmp_path):
    path = tmp_path / "corp.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CorpCodeUnavailable):
        load_mapping(path)


def test_빈_캐시를_거부한다(tmp_path):
    path = tmp_path / "corp.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CorpCodeUnavailable):
        load_mapping(path)


def test_형식이_틀린_캐시를_거부한다(tmp_path):
    """DartCorpCodeResolver 가 생성 시점에 던지기 전에 여기서 잡는다."""
    path = tmp_path / "corp.json"
    path.write_text(json.dumps({"5930": "00126380"}), encoding="utf-8")
    with pytest.raises(CorpCodeUnavailable, match="형식 오류"):
        load_mapping(path)


def test_적재한_매핑을_DartCorpCodeResolver가_받는다(tmp_path):
    """로더의 출력이 resolver 의 입력 계약을 만족해야 의미가 있다."""
    path = save_mapping(tmp_path / "corp.json", parse_corp_code_xml(XML))
    resolver = DartCorpCodeResolver(load_mapping(path))
    assert resolver.resolve("005930") == "00126380"


def test_영문_다섯번째_KRX코드는_cache와_DART_resolver에서_보존된다(tmp_path):
    mapping = parse_corp_code_xml(XML)
    path = save_mapping(tmp_path / "corp.json", mapping)
    loaded = load_mapping(path)
    assert loaded["0126Z0"] == "01965324"
    assert DartCorpCodeResolver(loaded).resolve("0126Z0") == "01965324"


# ── fetch ─────────────────────────────────────────────────────────


class _Response:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


class _Client:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self._response


async def test_fetch가_인증키를_쿼리로_보낸다():
    client = _Client(_Response(200, zipped(XML)))
    mapping = await fetch_corp_code_mapping(" key ", client)
    assert mapping["005930"] == "00126380"
    assert client.calls[0]["params"] == {"crtfc_key": "key"}


async def test_빈_인증키를_먼저_거부한다():
    client = _Client(_Response(200, zipped(XML)))
    with pytest.raises(CorpCodeUnavailable, match="DART_API_KEY"):
        await fetch_corp_code_mapping("  ", client)
    assert client.calls == [], "요청을 보내기 전에 멈춰야 한다"


async def test_HTTP_실패를_상태코드와_함께_알린다():
    client = _Client(_Response(503, b""))
    with pytest.raises(CorpCodeUnavailable, match="503"):
        await fetch_corp_code_mapping("key", client)
