"""Deterministic article-to-stock attribution rules from news_search_v5.

This layer answers only "is this article about the target stock?".  It does not
classify good/bad news; claim-relative support/oppose belongs to n7.
"""

from __future__ import annotations

import re

from .models import AttributionDecision, NaverEntityProfile

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+")
_KRX_CODE_RE = re.compile(r"(?<![0-9A-Z])[0-9]{5}[0-9A-Z](?![0-9A-Z])", re.IGNORECASE)
_MARKET_ROUNDUP_CODE_COUNT = 3


def _mask_affiliates(text: str, affiliates: tuple[str, ...]) -> str:
    masked = text
    for name in affiliates:
        if name:
            masked = masked.replace(name.lower(), " ")
    return masked


def judge_attribution(
    title: str,
    snippet: str,
    profile: NaverEntityProfile,
) -> AttributionDecision:
    """Return a deterministic relevance decision without using an LLM."""

    raw = f"{title or ''} {snippet or ''}"
    haystack = _EMAIL_RE.sub(" ", raw).lower()

    for term in profile.exclude_terms:
        if term and term.lower() in haystack:
            return AttributionDecision(False, f"excluded_term:{term}")

    masked = _mask_affiliates(haystack, profile.affiliates)
    inclusion = (profile.name, *profile.aliases, *profile.former_names, profile.code)
    matched = next((term for term in inclusion if term and term.lower() in masked), None)
    if matched is None:
        if any(name and name.lower() in haystack for name in profile.affiliates):
            return AttributionDecision(False, "affiliate_only")
        return AttributionDecision(False, "no_match")

    distinct_codes = {code.upper() for code in _KRX_CODE_RE.findall(raw.upper())}
    title_lower = (title or "").lower()
    name_in_title = any(
        term and term.lower() in title_lower
        for term in (profile.name, *profile.aliases, *profile.former_names)
    )
    if len(distinct_codes) >= _MARKET_ROUNDUP_CODE_COUNT and not name_in_title:
        return AttributionDecision(False, f"market_roundup:{len(distinct_codes)}codes")

    return AttributionDecision(True, f"matched:{matched}")
