"""Typed, non-canonical Provider execution failures."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.frozen import RateLimitHint, ReasonCode


@dataclass(frozen=True)
class ProviderExecutionError(RuntimeError):
    """A known adapter execution failure that has no raw response body."""

    reason_code: ReasonCode
    retryable: bool
    http_status: int | None = None
    rate_limit_hint: RateLimitHint | None = None
    safe_detail: str | None = None
