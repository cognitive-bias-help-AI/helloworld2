"""Provider-local immutable models.

These types deliberately do not depend on ``app``.  The gateway adapter is the
only bridge from the independent NAVER core into the application's frozen
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NaverEntityProfile:
    code: str
    name: str
    aliases: tuple[str, ...] = ()
    former_names: tuple[str, ...] = ()
    affiliates: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    curated: bool = False


@dataclass(frozen=True)
class AttributionDecision:
    is_relevant: bool
    reason: str
    strategy: str = "rule_enriched_v1"


@dataclass(frozen=True)
class NaverNewsRecord:
    title: str
    snippet: str
    link: str
    original_link: str | None
    publisher: str | None
    published_at: datetime | None

    @property
    def canonical_url(self) -> str:
        return self.original_link or self.link
