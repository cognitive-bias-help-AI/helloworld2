"""Shared deterministic indexing and matching for stock resolvers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.stock_scope import AssetType, InstrumentCandidate
from app.schemas.frozen import StockCandidate

_CODE_IN_TEXT = re.compile(r"(?<![0-9A-Z])([0-9]{5}[0-9A-Z])(?![0-9A-Z])")
_WS = re.compile(r"\s+")
_CHOSUNG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_SCORE = {"exact_code": 1.0, "exact_name": 1.0, "alias": 0.9, "prefix": 0.7, "chosung": 0.5}
_RANK = ("exact_code", "exact_name", "alias", "prefix", "chosung")


def normalize(text: str) -> str:
    return _WS.sub("", str(text or "")).replace("·", "").replace("-", "").upper()


def chosung_of(text: str) -> str:
    result = []
    for character in text:
        point = ord(character)
        if 0xAC00 <= point <= 0xD7A3:
            result.append(_CHOSUNG[(point - 0xAC00) // 588])
        elif not character.isspace():
            result.append(character.upper())
    return "".join(result)


@dataclass(frozen=True, slots=True)
class MatchableStock:
    code: str
    name: str
    market: str
    asset_type: AssetType
    aliases: tuple[str, ...] = ()
    is_delisted: bool = False
    is_managed: bool = False

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


class StockMatcher:
    def __init__(self, rows: tuple[MatchableStock, ...]) -> None:
        self._by_code = {row.code: row for row in rows}
        self._by_chosung: dict[str, list[MatchableStock]] = {}
        for row in rows:
            self._by_chosung.setdefault(chosung_of(row.name), []).append(row)
        self._contains = sorted(
            [(normalize(row.name), row, "exact_name") for row in rows]
            + [(normalize(alias), row, "alias") for row in rows for alias in row.aliases],
            key=lambda item: len(item[0]),
            reverse=True,
        )

    def resolve_exact(self, code: str) -> list[InstrumentCandidate]:
        row = self._by_code.get(str(code or "").strip())
        return [row.instrument] if row is not None else []

    def resolve(self, text: str, limit: int = 5) -> list[StockCandidate]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        query = str(text or "")
        found: dict[str, str] = {}
        specificity: dict[str, int] = {}
        for code in _CODE_IN_TEXT.findall(query.upper()):
            if code in self._by_code:
                found.setdefault(code, "exact_code")
        normalized = normalize(query)
        if normalized:
            remaining = normalized
            for needle, row, kind in self._contains:
                if not needle or needle not in remaining:
                    continue
                remaining = remaining.replace(needle, "\x00", 1)
                if row.code not in found or len(needle) > specificity.get(row.code, 0):
                    found[row.code] = kind
                    specificity[row.code] = len(needle)
            if not found:
                for row in self._by_code.values():
                    if normalize(row.name).startswith(normalized):
                        found[row.code] = "prefix"
                        specificity[row.code] = len(normalized)
                for row in self._by_chosung.get(chosung_of(query), ()):
                    if row.code not in found:
                        found[row.code] = "chosung"
                        specificity[row.code] = len(normalized)
        candidates = [
            StockCandidate(
                code=code,
                name=self._by_code[code].name,
                market=self._by_code[code].market,
                match_kind=kind,
                score=_SCORE[kind],
                is_delisted=self._by_code[code].is_delisted,
                is_managed=self._by_code[code].is_managed,
            )
            for code, kind in found.items()
        ]
        candidates.sort(key=lambda item: (_RANK.index(item.match_kind), -specificity.get(item.code, 0), item.code))
        return candidates[:limit]
