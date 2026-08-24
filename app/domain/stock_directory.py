"""CSV 기반 StockResolver 구현.

🔴 이것은 KRX 마스터가 아니다.

`data/stock_directory.csv` 는 **데모 대상 종목만 담은 seed 파일**이다. 전체
상장 종목 마스터와 그 갱신(신규상장·상장폐지·관리종목 지정)은 팀원1 라인의
`app/domain/stock_master.py` 가 맡기로 되어 있고, 그것이 생기면 이 파일은
그쪽을 가리키거나 폐기한다. 파일명을 `stock_master.py` 로 잡지 않은 이유가
그것이다 — 남의 자리를 미리 차지하면 나중에 합치기가 더 비싸다.

지금 이 파일이 존재하는 이유는 하나다: `RuntimeDeps.stock_resolver` 가
필수라서 이게 없으면 그래프가 아예 못 돈다.

■ 매칭 규칙 (`StockCandidate.match_kind`)

    exact_code   6자리 코드 완전일치            score 1.00
    exact_name   정규화된 종목명 완전일치        score 1.00
    alias        등록된 별칭 완전일치            score 0.90
    prefix       종목명이 질의로 시작            score 0.70
    chosung      초성 완전일치 (ㅅㅅㅈㅈ)        score 0.50

자연어 문장에서 종목을 찾아야 하므로, 질의 전체가 안 맞으면 **문장 안에
종목명·별칭이 포함되어 있는지**도 본다. 이때 긴 이름을 먼저 보므로
"삼성전자우" 가 "삼성전자" 보다 우선 매칭된다.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.domain.stock_matcher import MatchableStock, StockMatcher, chosung_of, normalize
from app.domain.stock_scope import AssetType, InstrumentCandidate
from app.schemas.frozen import StockCandidate

_KRX_CODE: Final = re.compile(r"^[0-9]{5}[0-9A-Z]$")
@dataclass(frozen=True, slots=True)
class _Row:
    code: str
    name: str
    market: str
    asset_type: AssetType
    aliases: tuple[str, ...]
    is_delisted: bool
    is_managed: bool

    @property
    def instrument(self) -> InstrumentCandidate:
        return InstrumentCandidate(
            code=self.code,
            name=self.name,
            market=self.market,
            asset_type=self.asset_type,
            is_delisted=self.is_delisted,
            is_managed=self.is_managed,
        )


def _flag(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "y", "yes"}


def load_rows(path: Path) -> tuple[_Row, ...]:
    """CSV 를 읽어 검증한다. 형식이 틀리면 여기서 멈춘다.

    조용히 건너뛰지 않는 이유: 한 줄이 빠지면 그 종목만 "없는 종목" 이 되고,
    n2 가 STOCK_UNRESOLVED 로 막는다. 원인을 데이터에서 찾기 어렵다.
    """
    if not path.exists():
        raise FileNotFoundError(f"stock directory not found: {path}")
    rows: list[_Row] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for index, record in enumerate(csv.DictReader(handle), start=2):
            code = str(record.get("code") or "").strip()
            name = str(record.get("name") or "").strip()
            market = str(record.get("market") or "").strip().upper()
            if _KRX_CODE.fullmatch(code) is None:
                raise ValueError(f"{path}:{index} 종목코드 형식 오류: {code!r}")
            if code in seen:
                raise ValueError(f"{path}:{index} 종목코드 중복: {code}")
            if not name:
                raise ValueError(f"{path}:{index} 종목명이 비어 있다: {code}")
            if market not in {"KOSPI", "KOSDAQ"}:
                raise ValueError(f"{path}:{index} 시장 구분 오류: {market!r}")
            try:
                asset_type = AssetType(str(record.get("asset_type") or "").strip().upper())
            except ValueError as exc:
                raise ValueError(
                    f"{path}:{index} asset_type 오류: {record.get('asset_type')!r}"
                ) from exc
            seen.add(code)
            rows.append(
                _Row(
                    code=code,
                    name=name,
                    market=market,
                    asset_type=asset_type,
                    aliases=tuple(
                        item.strip()
                        for item in str(record.get("aliases") or "").split("|")
                        if item.strip()
                    ),
                    is_delisted=_flag(record.get("is_delisted", "")),
                    is_managed=_flag(record.get("is_managed", "")),
                )
            )
    if not rows:
        raise ValueError(f"{path} 에 종목이 없다")
    return tuple(rows)


class CsvStockDirectory:
    """`StockResolver` Protocol 구현. I/O 는 생성 시점에 한 번만 한다."""

    def __init__(self, rows: tuple[_Row, ...]) -> None:
        self._rows = rows
        self._matcher = StockMatcher(
            tuple(
                MatchableStock(
                    code=row.code,
                    name=row.name,
                    market=row.market,
                    asset_type=row.asset_type,
                    aliases=row.aliases,
                    is_delisted=row.is_delisted,
                    is_managed=row.is_managed,
                )
                for row in rows
            )
        )

    @classmethod
    def from_csv(cls, path: str | Path) -> CsvStockDirectory:
        return cls(load_rows(Path(path)))

    # ── StockResolver Protocol ────────────────────────────────────

    def resolve_exact(self, code: str) -> list[InstrumentCandidate]:
        """코드 완전일치. n2 가 사용자 선택 코드를 재확인할 때 쓴다."""
        return self._matcher.resolve_exact(code)

    def resolve(self, text: str, limit: int = 5) -> list[StockCandidate]:
        """자연어에서 종목 후보를 찾는다. 점수 내림차순, 동점이면 코드순."""
        return self._matcher.resolve(text, limit)


__all__ = ["CsvStockDirectory", "chosung_of", "load_rows", "normalize"]
