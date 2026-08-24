"""Main-application ProviderAdapter bridge for the independent NAVER core."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal
from urllib.parse import urlparse

import httpx

from app.gateway.execution import ProviderExecutionError
from app.schemas.frozen import EvidenceDraft, Query, RateLimitHint, ReasonCode, Request
from providers.naver.attribution import judge_attribution
from providers.naver.client import NAVER_NEWS_SEARCH_URL, NaverNewsClient
from providers.naver.curation import load_profile
from providers.naver.dedup import deduplicate_records, normalize_url
from providers.naver.models import NaverNewsRecord
from providers.naver.text import http_url, parse_pubdate, publisher_host, strip_html

_SEMANTIC_ENDPOINT = "news_search"
_ALLOWED_SORTS = {"date", "sim"}


def _stock_code(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 6
        or not value[:4].isdigit()
        or any(not (character.isdigit() or "A" <= character <= "Z") for character in value[4:])
    ):
        raise ValueError("stock_code must be a six-character KRX code")
    return value


def _query_contract(q: Query) -> tuple[str, str, str, int, str]:
    if q.provider != "naver":
        raise ValueError("Query provider does not match NaverAdapter")
    if q.endpoint != _SEMANTIC_ENDPOINT:
        raise ValueError(f"unsupported NAVER endpoint: {q.endpoint}")
    required = {"stock_code", "stock_name", "query", "display", "sort"}
    if set(q.params) != required:
        raise ValueError("news_search params do not match v1 contract")
    stock_code = _stock_code(q.params["stock_code"])
    stock_name = q.params["stock_name"]
    query_text = q.params["query"]
    display = q.params["display"]
    sort = q.params["sort"]
    if not isinstance(stock_name, str) or not stock_name.strip():
        raise ValueError("stock_name must be non-blank")
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValueError("query must be non-blank")
    if not isinstance(display, int) or isinstance(display, bool) or not 1 <= display <= 100:
        raise ValueError("display must be between 1 and 100")
    if sort not in _ALLOWED_SORTS:
        raise ValueError("sort must be date or sim")
    return stock_code, stock_name.strip(), query_text.strip(), display, sort


def _parse_item(item: object) -> NaverNewsRecord | None:
    if not isinstance(item, dict):
        raise ValueError("NAVER items must contain objects")
    title = strip_html(item.get("title"))
    snippet = strip_html(item.get("description"))
    link = http_url(item.get("link"))
    original = http_url(item.get("originallink"))
    canonical = original or link
    if canonical is None or not (title or snippet):
        return None
    return NaverNewsRecord(
        title=title,
        snippet=snippet,
        link=link or canonical,
        original_link=original,
        publisher=publisher_host(original, link),
        published_at=parse_pubdate(item.get("pubDate")),
    )


def _raw_span(record: NaverNewsRecord) -> str:
    if record.title and record.snippet:
        value = f"{record.title} — {record.snippet}"
    else:
        value = record.title or record.snippet
    value = " ".join(value.split())
    return value[:500]


def _source_ref(record: NaverNewsRecord) -> str:
    normalized = normalize_url(record.canonical_url)
    if not normalized:
        raise ValueError("NAVER article requires a canonical URL")
    return "naver:" + sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _draft(
    record: NaverNewsRecord,
    *,
    stock_code: str,
    stock_name: str,
    query_text: str,
    attribution_reason: str,
) -> EvidenceDraft:
    source_url = record.canonical_url
    if urlparse(source_url).scheme not in {"http", "https"}:
        raise ValueError("NAVER article source_url must be http(s)")
    return EvidenceDraft(
        source_type="news",
        source_ref=_source_ref(record),
        source_url=source_url,
        publisher=record.publisher,
        published_at=record.published_at,
        raw_span=_raw_span(record),
        span_scope="headline_snippet",
        normalized_value={
            "kind": "news",
            "provider": "naver",
            "stock_code": stock_code,
            "stock_name": stock_name,
            "query": query_text,
            "title": record.title,
            "snippet": record.snippet,
            "naver_url": record.link,
            "original_url": record.original_link,
            "attribution_reason": attribution_reason,
            "attribution_strategy": "rule_enriched_v1",
        },
    )


def _meta(raw: dict) -> dict:
    value = raw.get("_meta")
    if not isinstance(value, dict):
        raise ValueError("NAVER raw response lacks _meta")
    return value


def _status(raw: dict) -> int:
    status = _meta(raw).get("http_status")
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        raise ValueError("NAVER raw response has invalid http_status")
    return status


class NaverAdapter:
    name: Literal["naver"] = "naver"
    max_concurrency = 3

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = NaverNewsClient(client_id, client_secret, client)

    def build_request(self, q: Query, as_of: datetime) -> Request:
        del as_of
        _, _, query_text, display, sort = _query_contract(q)
        return Request(
            provider="naver",
            endpoint=NAVER_NEWS_SEARCH_URL,
            method="GET",
            params={"query": query_text, "display": display, "sort": sort},
            timeout_s=10.0,
        )

    async def acall(self, req: Request) -> dict:
        if (
            req.provider != self.name
            or req.endpoint != NAVER_NEWS_SEARCH_URL
            or req.method != "GET"
            or set(req.params) != {"query", "display", "sort"}
        ):
            raise ValueError("Request does not belong to NaverAdapter")
        try:
            return await self._client.search(
                query=req.params["query"],
                display=req.params["display"],
                sort=req.params["sort"],
                timeout_s=req.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderExecutionError(
                reason_code=ReasonCode.UPSTREAM_TIMEOUT,
                retryable=True,
                safe_detail="NAVER news request timed out",
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderExecutionError(
                reason_code=ReasonCode.UPSTREAM_TIMEOUT,
                retryable=True,
                safe_detail="NAVER news network failure",
            ) from exc

    def parse_response(self, raw: dict, q: Query) -> list[EvidenceDraft]:
        stock_code, stock_name, query_text, display, sort = _query_contract(q)
        if not 200 <= _status(raw) < 300:
            raise ValueError("NAVER error response cannot be parsed as EvidenceDraft")
        meta = _meta(raw)
        if (
            meta.get("request_query") != query_text
            or meta.get("request_display") != display
            or meta.get("request_sort") != sort
        ):
            raise ValueError("NAVER response request lineage does not match Query")
        body = raw.get("body")
        if not isinstance(body, dict):
            raise ValueError("NAVER success body must be an object")
        items = body.get("items")
        if not isinstance(items, list):
            raise ValueError("NAVER success body requires items list")

        profile = load_profile(stock_code, stock_name)
        accepted: list[tuple[NaverNewsRecord, str]] = []
        for item in items:
            record = _parse_item(item)
            if record is None:
                continue
            decision = judge_attribution(record.title, record.snippet, profile)
            if decision.is_relevant:
                accepted.append((record, decision.reason))

        reason_by_url: dict[str, str] = {}
        for record, reason in accepted:
            reason_by_url.setdefault(normalize_url(record.canonical_url), reason)
        records = deduplicate_records([record for record, _ in accepted])
        return [
            _draft(
                record,
                stock_code=stock_code,
                stock_name=stock_name,
                query_text=query_text,
                attribution_reason=reason_by_url.get(
                    normalize_url(record.canonical_url), "matched:dedup_representative"
                ),
            )
            for record in records
        ]

    def classify_error(self, raw: dict) -> tuple[ReasonCode, bool]:
        status = _status(raw)
        if 200 <= status < 300:
            raise ValueError("NAVER success response has no classifiable error")
        if status in {401, 403}:
            return ReasonCode.AUTH_FAILED, False
        if status == 408:
            return ReasonCode.UPSTREAM_TIMEOUT, True
        if status == 429:
            return ReasonCode.RATE_LIMIT, True
        if 500 <= status <= 599:
            return ReasonCode.UPSTREAM_5XX, True
        if 400 <= status <= 499:
            return ReasonCode.SCHEMA_INVALID, False
        return ReasonCode.CONTRACT_VIOLATION, False

    def rate_limit_hint(self, raw: dict) -> RateLimitHint | None:
        try:
            reason, _ = self.classify_error(raw)
        except ValueError:
            return None
        if reason is not ReasonCode.RATE_LIMIT:
            return None
        headers = _meta(raw).get("headers")
        if not isinstance(headers, dict):
            return RateLimitHint(provider="naver", source="status_only")
        lowered = {str(key).lower(): str(value) for key, value in headers.items()}

        def nonnegative_int(name: str) -> int | None:
            value = lowered.get(name)
            if value is None:
                return None
            try:
                parsed = int(value)
            except ValueError as exc:
                raise ValueError(f"invalid NAVER {name} header") from exc
            if parsed < 0:
                raise ValueError(f"invalid NAVER {name} header")
            return parsed

        retry_after_s = nonnegative_int("retry-after")
        remaining = nonnegative_int("x-ratelimit-remaining")
        window_s = nonnegative_int("x-ratelimit-window")
        if retry_after_s is None and remaining is None and window_s is None:
            return RateLimitHint(provider="naver", source="status_only")
        return RateLimitHint(
            provider="naver",
            retry_after_ms=None if retry_after_s is None else retry_after_s * 1000,
            remaining=remaining,
            window_s=window_s,
            source="header",
        )


__all__ = ["NAVER_NEWS_SEARCH_URL", "NaverAdapter"]
