"""Small measured curation layer carried over from news_search_v5.

The file is intentionally a *small exception list*, not a KRX master.  n2 owns
stock resolution.  This module only supplies aliases/affiliates/exclusion terms
that improved NAVER attribution precision in the v5 experiments.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .models import NaverEntityProfile

_CURATED_PATH = Path(__file__).with_name("data") / "stock_curation.json"


@lru_cache(maxsize=1)
def _curated_by_code() -> dict[str, dict]:
    try:
        payload = json.loads(_CURATED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(payload, list):
        return {}
    return {
        str(item["code"]): item
        for item in payload
        if isinstance(item, dict) and item.get("code")
    }


def is_curated(stock_code: str) -> bool:
    return stock_code in _curated_by_code()


def load_profile(stock_code: str, stock_name: str) -> NaverEntityProfile:
    item = _curated_by_code().get(stock_code)
    if item is None:
        return NaverEntityProfile(code=stock_code, name=stock_name, curated=False)
    return NaverEntityProfile(
        code=stock_code,
        name=stock_name or str(item.get("name") or stock_code),
        aliases=tuple(str(x) for x in item.get("aliases", []) if str(x).strip()),
        former_names=tuple(str(x) for x in item.get("former_names", []) if str(x).strip()),
        affiliates=tuple(str(x) for x in item.get("affiliates", []) if str(x).strip()),
        exclude_terms=tuple(str(x) for x in item.get("exclude_terms", []) if str(x).strip()),
        curated=True,
    )
