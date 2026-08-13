"""Deterministic ProviderAdapter reference implementation."""

from datetime import datetime
from typing import Literal

from app.schemas.frozen import (
    PROVIDER_SOURCE_TYPE,
    EvidenceDraft,
    Query,
    RateLimitHint,
    ReasonCode,
    Request,
)


class MockAdapter:
    def __init__(self, provider: Literal["dart", "naver", "kiwoom"]):
        self.name = provider
        self.max_concurrency = 1 if provider == "kiwoom" else 3

    def build_request(self, q: Query, as_of: datetime) -> Request:
        if q.provider != self.name:
            raise ValueError("Query provider가 adapter와 다름")
        return Request(
            provider=self.name, endpoint=q.endpoint, params={**q.params, "as_of": as_of.isoformat()}
        )

    async def acall(self, req: Request) -> dict:
        return {"provider": req.provider, "endpoint": req.endpoint, "params": req.params}

    def parse_response(self, raw: dict, q: Query) -> list[EvidenceDraft]:
        published = datetime.fromisoformat("2026-08-11T15:30:00+09:00")
        values = {
            "dart": dict(
                source_ref="20250814000123",
                source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250814000123",
                publisher="삼성전자",
                raw_span="2025년 3분기 영업이익 9,178,955백만원",
                span_scope="structured_field",
                normalized_value={
                    "metric": "operating_profit",
                    "value": 9178955000000,
                    "unit": "KRW",
                    "period": "2025Q3",
                },
            ),
            "naver": dict(
                source_ref="news-0001",
                source_url="https://news.example.com/0001",
                publisher="예시경제",
                raw_span="삼성전자 실적 전망 관련 뉴스",
                span_scope="headline_snippet",
                normalized_value={"title": "삼성전자 실적 전망", "company": "삼성전자"},
            ),
            "kiwoom": dict(
                source_ref="ka10001:005930",
                source_url=None,
                publisher="키움증권",
                raw_span="2026-08-11 종가 71,800원, 전일대비 +1.24%",
                span_scope="structured_field",
                normalized_value={
                    "close": 71800,
                    "chg_pct": 1.24,
                    "volume": 12345678,
                    "trade_date": "2026-08-11",
                },
            ),
        }[self.name]
        return [
            EvidenceDraft(
                source_type=PROVIDER_SOURCE_TYPE[self.name], published_at=published, **values
            )
        ]

    def classify_error(self, raw: dict) -> tuple[ReasonCode, bool]:
        if raw.get("timeout"):
            return ReasonCode.UPSTREAM_TIMEOUT, True
        status = raw.get("status")
        if status == 429:
            return ReasonCode.RATE_LIMIT, True
        if status in (401, 403):
            return ReasonCode.AUTH_FAILED, False
        if isinstance(status, int) and 500 <= status <= 599:
            return ReasonCode.UPSTREAM_5XX, True
        raise ValueError("분류할 수 없는 mock error")

    def rate_limit_hint(self, raw: dict) -> RateLimitHint | None:
        if raw.get("status") != 429:
            return None
        return RateLimitHint(
            provider=self.name, retry_after_ms=1000, remaining=0, window_s=1, source="status_only"
        )
