"""Provider adapter와 replay cache의 P0-3 인터페이스 계약."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from app.schemas.frozen import EvidenceDraft, Query, RateLimitHint, ReasonCode, Request


@runtime_checkable
class ProviderAdapter(Protocol):
    name: Literal["dart", "naver", "kiwoom"]
    max_concurrency: int

    def build_request(self, q: Query, as_of: datetime) -> Request: ...

    async def acall(self, req: Request) -> dict: ...

    def parse_response(self, raw: dict, q: Query) -> list[EvidenceDraft]: ...

    def classify_error(self, raw: dict) -> tuple[ReasonCode, bool]: ...

    def rate_limit_hint(self, raw: dict) -> RateLimitHint | None: ...


@runtime_checkable
class ReplayCache(Protocol):
    def make_key(
        self, provider: str, endpoint: str, params: dict, as_of: datetime
    ) -> str: ...

    async def get(self, key: str) -> dict | None: ...

    async def put(self, key: str, raw: dict, ttl_s: int) -> None: ...

    async def record(self, key: str, raw: dict) -> None: ...
