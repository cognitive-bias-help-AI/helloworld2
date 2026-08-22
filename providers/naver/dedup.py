"""High-precision NAVER redistribution/event dedup carried over from v5.

Exact URL matches are merged transitively.  Text-based merging uses
complete-linkage so A~B and B~C cannot silently force A~C into one event.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse

from .models import NaverNewsRecord

_NUM_RE = re.compile(r"(?<!\d)\d{3,5}(?!\d)")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

TITLE_SIM_MIN = 0.40
TITLE_TOKEN_MIN = 0.30
SNIPPET_SIM_MIN = 0.45
MAX_TIME_GAP = timedelta(hours=48)


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        )
    )


def _normalize_text(text: str) -> str:
    value = _PUNCT_RE.sub(" ", text or "")
    return _WS_RE.sub(" ", value).strip().lower()


def _numbers(record: NaverNewsRecord) -> set[str]:
    found = set(_NUM_RE.findall(f"{record.title} {record.snippet}"))
    return {item for item in found if _YEAR_RE.fullmatch(item) is None}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _time_ok(left: NaverNewsRecord, right: NaverNewsRecord) -> bool:
    if left.published_at is None or right.published_at is None:
        return True
    return abs(_aware(left.published_at) - _aware(right.published_at)) <= MAX_TIME_GAP


def _same_event(left: NaverNewsRecord, right: NaverNewsRecord) -> bool:
    if not _time_ok(left, right):
        return False

    title_left = _normalize_text(left.title)
    title_right = _normalize_text(right.title)
    if not title_left or not title_right:
        return False

    title_similarity = SequenceMatcher(None, title_left, title_right).ratio()
    tokens_left, tokens_right = set(title_left.split()), set(title_right.split())
    union = tokens_left | tokens_right
    token_jaccard = len(tokens_left & tokens_right) / len(union) if union else 0.0
    if title_similarity < TITLE_SIM_MIN or token_jaccard < TITLE_TOKEN_MIN:
        return False

    numbers_left, numbers_right = _numbers(left), _numbers(right)
    if numbers_left and numbers_right and not (numbers_left & numbers_right):
        return False

    snippet_left = _normalize_text(left.snippet)
    snippet_right = _normalize_text(right.snippet)
    if snippet_left and snippet_right:
        return SequenceMatcher(None, snippet_left, snippet_right).ratio() >= SNIPPET_SIM_MIN
    return True


def _representative_key(record: NaverNewsRecord):
    if record.published_at is None:
        return (1, datetime.max.replace(tzinfo=UTC), normalize_url(record.canonical_url))
    return (0, _aware(record.published_at), normalize_url(record.canonical_url))


def deduplicate_records(records: list[NaverNewsRecord]) -> list[NaverNewsRecord]:
    """Return one deterministic representative for each exact/reprint cluster."""

    # 1. Exact canonical URL clusters.
    by_url: dict[str, list[NaverNewsRecord]] = {}
    url_less: list[NaverNewsRecord] = []
    for record in records:
        key = normalize_url(record.canonical_url)
        if key:
            by_url.setdefault(key, []).append(record)
        else:
            url_less.append(record)

    url_clusters = [*by_url.values(), *([item] for item in url_less)]

    # 2. Complete-linkage across exact-URL clusters.
    clusters: list[list[NaverNewsRecord]] = []
    for members in url_clusters:
        head = members[0]
        for cluster in clusters:
            if all(_same_event(head, existing) for existing in cluster):
                cluster.extend(members)
                break
        else:
            clusters.append(list(members))

    representatives = [min(cluster, key=_representative_key) for cluster in clusters]
    return sorted(representatives, key=_representative_key)
