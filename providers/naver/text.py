"""NAVER response text/date normalization."""

from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", text))).strip()


def parse_pubdate(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        try:
            value = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
    return value if value.tzinfo is not None else None


def http_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = html.unescape(value.strip())
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def publisher_host(original_link: str | None, link: str | None) -> str | None:
    for value in (original_link, link):
        if value:
            host = urlparse(value).netloc.lower().removeprefix("www.")
            if host:
                return host
    return None
