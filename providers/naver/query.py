"""Deterministic NAVER query translation.

One returned params object means one upstream HTTP call.  This is deliberate:
``EvidenceGateway`` accounts external-call budget and provenance per Query, so
an adapter must not hide a name/code union as two internal requests.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from .curation import is_curated

QueryStrategy = Literal[
    "name_only",
    "code_only",
    "name_plus_code",
    "name_code_union",
    "curation_adaptive",
]

_KRX_CODE_RE = re.compile(r"^[0-9]{4}[0-9A-Z]{2}$")
_ALLOWED_SORTS = {"date", "sim"}


def _validate_identity(stock_code: str, stock_name: str) -> None:
    if not isinstance(stock_code, str) or _KRX_CODE_RE.fullmatch(stock_code) is None:
        raise ValueError("stock_code must be a six-character KRX code")
    if not isinstance(stock_name, str) or not stock_name.strip():
        raise ValueError("stock_name must be non-blank")


def _unique_nonblank(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("NAVER query text must be non-blank")
        normalized = value.strip()
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    if not result:
        raise ValueError("at least one NAVER query is required")
    return tuple(result)


def build_search_terms(
    stock_code: str,
    stock_name: str,
    *,
    strategy: QueryStrategy = "curation_adaptive",
    supplied_queries: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return source-specific query strings only; no network call is performed.

    The default reproduces the measured v5 rule:
    - curated/collision-prone stocks -> code only (precision first)
    - uncurated stocks -> name + code (recall recovery)
    """

    _validate_identity(stock_code, stock_name)
    if supplied_queries is not None:
        return _unique_nonblank(supplied_queries)

    name = stock_name.strip()
    if strategy == "name_only":
        values = (name,)
    elif strategy == "code_only":
        values = (stock_code,)
    elif strategy == "name_plus_code":
        values = (f"{name} {stock_code}",)
    elif strategy == "name_code_union":
        values = (name, stock_code)
    elif strategy == "curation_adaptive":
        values = (stock_code,) if is_curated(stock_code) else (name, stock_code)
    else:
        raise ValueError(f"unsupported NAVER query strategy: {strategy}")
    return _unique_nonblank(values)


def build_query_params(
    stock_code: str,
    stock_name: str,
    *,
    strategy: QueryStrategy = "curation_adaptive",
    supplied_queries: Iterable[str] | None = None,
    display: int = 30,
    sort: Literal["date", "sim"] = "date",
) -> tuple[dict[str, object], ...]:
    """Build one n5-compatible Query.params payload per real NAVER call."""

    _validate_identity(stock_code, stock_name)
    if not isinstance(display, int) or isinstance(display, bool) or not 1 <= display <= 100:
        raise ValueError("display must be an integer between 1 and 100")
    if sort not in _ALLOWED_SORTS:
        raise ValueError("sort must be 'date' or 'sim'")
    terms = build_search_terms(
        stock_code,
        stock_name,
        strategy=strategy,
        supplied_queries=supplied_queries,
    )
    return tuple(
        {
            "stock_code": stock_code,
            "stock_name": stock_name.strip(),
            "query": term,
            "display": display,
            "sort": sort,
        }
        for term in terms
    )
