"""Deterministic stock-code to OpenDART corp-code resolution."""

from __future__ import annotations

import re
from collections.abc import Mapping

_STOCK_CODE = re.compile(r"\d{6}")
_CORP_CODE = re.compile(r"\d{8}")


class DartCorpCodeResolver:
    """Resolve only from an already-loaded mapping; never performs I/O."""

    def __init__(self, mapping: Mapping[str, str]) -> None:
        values = dict(mapping)
        if any(_STOCK_CODE.fullmatch(key) is None for key in values):
            raise ValueError("DART stock_code must be six digits")
        if any(_CORP_CODE.fullmatch(value) is None for value in values.values()):
            raise ValueError("DART corp_code must be eight digits")
        self._mapping = values

    def resolve(self, stock_code: str) -> str:
        if _STOCK_CODE.fullmatch(stock_code) is None:
            raise ValueError("DART stock_code must be six digits")
        try:
            return self._mapping[stock_code]
        except KeyError as exc:
            raise ValueError(f"unknown DART stock_code: {stock_code}") from exc
