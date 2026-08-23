"""OpenDART corpCode 원본을 stock_code -> corp_code 매핑으로 만든다.

`DartCorpCodeResolver` 는 이미 만들어진 매핑만 받고 I/O 를 하지 않는다.
그 매핑을 만드는 쪽이 없어서 production bootstrap 이 caller 에게 매핑을
요구하고 있었다 — 이 파일이 그 자리를 채운다.

■ 원본 형태

OpenDART `corpCode.xml` 엔드포인트는 XML 이 아니라 **ZIP** 을 돌려준다.
압축 안에 `CORPCODE.xml` 한 장이 들어 있고 항목은 이렇게 생겼다.

    <result>
      <list>
        <corp_code>00126380</corp_code>
        <corp_name>삼성전자</corp_name>
        <stock_code>005930</stock_code>
        <modify_date>20240101</modify_date>
      </list>
      ...
    </result>

비상장 법인은 `stock_code` 가 비어 있다. 전체 10만 건 중 상장분은 3천 건대다.

■ 🔴 stock_code 가 6자리 숫자인 것만 담는다

`KRXCode` 는 `[0-9]{5}[0-9A-Z]` 라 `00088K` 같은 문자 코드를 허용하지만,
그건 신주인수권증서·전환사채처럼 **발행 증권(Security)** 쪽 코드다.
DART 는 **발행 법인(Issuer)** 단위라 그런 코드를 싣지 않는다.

따라서 여기서 숫자 6자리만 남기는 것은 데이터 손실이 아니라 도메인 차이다.
우선주(005935)는 숫자라서 그대로 들어온다. Security -> Issuer 변환이 필요한
경우는 StockResolver 쪽에서 풀 문제이지 이 로더가 넓힐 문제가 아니다.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree

CORP_CODE_URL: Final = "https://opendart.fss.or.kr/api/corpCode.xml"
_STOCK_CODE: Final = re.compile(r"^\d{6}$")
_CORP_CODE: Final = re.compile(r"^\d{8}$")
_XML_MEMBER: Final = "CORPCODE.xml"


class CorpCodeUnavailable(RuntimeError):
    """corpCode 원본을 얻거나 해석하지 못했다."""


def parse_corp_code_xml(payload: bytes | str) -> dict[str, str]:
    """CORPCODE.xml 본문에서 상장 종목 매핑을 뽑는다.

    형식이 깨진 개별 항목은 건너뛴다. 여기서 멈추면 OpenDART 가 항목 하나를
    이상하게 내보낸 날 전체 매핑이 죽는다 — 상장 3천 건 중 1건이다.
    다만 **결과가 비면 실패로 본다**: 그건 형식이 통째로 바뀐 경우다.
    """
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise CorpCodeUnavailable(f"corpCode XML 파싱 실패: {exc}") from exc

    mapping: dict[str, str] = {}
    for item in root.iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if _STOCK_CODE.fullmatch(stock_code) is None:
            continue  # 비상장 법인
        if _CORP_CODE.fullmatch(corp_code) is None:
            continue
        mapping.setdefault(stock_code, corp_code)

    if not mapping:
        raise CorpCodeUnavailable(
            "corpCode XML 에서 상장 종목을 하나도 찾지 못했다. 응답 형식을 확인하라."
        )
    return mapping


def parse_corp_code_zip(payload: bytes) -> dict[str, str]:
    """OpenDART 가 돌려주는 ZIP 바이트에서 매핑을 뽑는다."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        # 인증키가 틀리면 ZIP 이 아니라 에러 XML 이 온다. 그 경우를 구분해서 알린다.
        text = payload[:400].decode("utf-8", errors="replace")
        raise CorpCodeUnavailable(
            f"corpCode 응답이 ZIP 이 아니다. 인증키를 확인하라. 응답 앞부분: {text!r}"
        ) from exc
    names = archive.namelist()
    member = next((n for n in names if n.upper().endswith(_XML_MEMBER.upper())), None)
    if member is None:
        raise CorpCodeUnavailable(f"ZIP 안에 {_XML_MEMBER} 가 없다: {names}")
    return parse_corp_code_xml(archive.read(member))


async def fetch_corp_code_mapping(api_key: str, client: Any) -> dict[str, str]:
    """OpenDART 에서 원본을 받아 매핑을 만든다.

    client 는 `httpx.AsyncClient` 를 기대하지만 타입으로 묶지 않는다 —
    이 모듈이 httpx 에 의존할 이유가 없고, 테스트가 가짜를 넣기 쉬워진다.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise CorpCodeUnavailable("DART_API_KEY 가 비어 있다")
    response = await client.get(
        CORP_CODE_URL, params={"crtfc_key": api_key.strip()}, timeout=60.0
    )
    status = getattr(response, "status_code", None)
    if status != 200:
        raise CorpCodeUnavailable(f"corpCode 요청 실패: HTTP {status}")
    return parse_corp_code_zip(response.content)


def save_mapping(path: str | Path, mapping: dict[str, str]) -> Path:
    """매핑을 JSON 으로 저장한다. 데모마다 10만 건을 다시 받지 않기 위한 캐시다.

    `.gitignore` 가 `data/*.json` 을 막고 있으므로 커밋되지 않는다 —
    파생물이고 크기가 크므로 그게 맞다.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(mapping, ensure_ascii=False, sort_keys=True, indent=0),
        encoding="utf-8",
    )
    return target


def load_mapping(path: str | Path) -> dict[str, str]:
    """저장해 둔 매핑을 읽는다. 형식 검증까지 여기서 끝낸다."""
    target = Path(path)
    if not target.exists():
        raise CorpCodeUnavailable(
            f"corp-code 캐시가 없다: {target}. "
            f"`python -m app.cli corp-code` 로 한 번 받아라."
        )
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpCodeUnavailable(f"corp-code 캐시가 깨졌다: {target}") from exc
    if not isinstance(loaded, dict) or not loaded:
        raise CorpCodeUnavailable(f"corp-code 캐시가 비었다: {target}")
    bad = [
        key
        for key, value in loaded.items()
        if _STOCK_CODE.fullmatch(str(key)) is None
        or _CORP_CODE.fullmatch(str(value)) is None
    ]
    if bad:
        raise CorpCodeUnavailable(
            f"corp-code 캐시에 형식 오류 {len(bad)}건: {bad[:5]}"
        )
    return {str(key): str(value) for key, value in loaded.items()}


__all__ = [
    "CORP_CODE_URL",
    "CorpCodeUnavailable",
    "fetch_corp_code_mapping",
    "load_mapping",
    "parse_corp_code_xml",
    "parse_corp_code_zip",
    "save_mapping",
]
