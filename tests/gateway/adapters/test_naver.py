import importlib
from datetime import UTC, datetime

import httpx
import pytest

from app.gateway.execution import ProviderExecutionError
from app.schemas.frozen import Query, ReasonCode

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def _adapter_module():
    try:
        return importlib.import_module("app.gateway.adapters.naver")
    except ModuleNotFoundError as exc:
        pytest.fail(f"app.gateway.adapters.naver is not implemented: {exc}")


def _query(**updates):
    values = {
        "query_id": "01K5ZTQ9X7WPCVN2M4H8JRAC3D",
        "scope": "claim",
        "claim_id": "01K5ZTQ9X7WPCVN2M4H8JRAC4D",
        "intent": "verify",
        "provider": "naver",
        "endpoint": "news_search",
        "params": {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "query": "005930",
            "display": 30,
            "sort": "date",
        },
        "created_at": NOW,
    }
    values.update(updates)
    return Query(**values)


def _raw_success():
    return {
        "_meta": {
            "http_status": 200,
            "headers": {},
            "request_query": "005930",
            "request_display": 30,
            "request_sort": "date",
        },
        "body": {
            "items": [
                {
                    "title": "<b>삼성전자</b>, HBM4 공급 확대",
                    "description": "삼성전자(<b>005930</b>)가 HBM4 공급 확대 계획을 밝혔다.",
                    "link": "https://n.news.naver.com/mnews/article/001/123?sid=101",
                    "originallink": "https://www.yna.co.kr/view/AKR123",
                    "pubDate": "Tue, 18 Aug 2026 09:00:00 +0900",
                },
                {
                    "title": "기자 연락처 안내",
                    "description": "문의 reporter@naver.com",
                    "link": "https://example.com/unrelated",
                    "originallink": "https://example.com/unrelated",
                    "pubDate": "Tue, 18 Aug 2026 08:00:00 +0900",
                },
            ]
        },
    }


def test_build_request_uses_semantic_endpoint_and_hides_credentials():
    mod = _adapter_module()
    adapter = mod.NaverAdapter("client-id", "client-secret")
    req = adapter.build_request(_query(), NOW)
    assert req.provider == "naver"
    assert req.endpoint == mod.NAVER_NEWS_SEARCH_URL
    assert req.method == "GET"
    assert req.params == {"query": "005930", "display": 30, "sort": "date"}
    assert req.headers == {}
    assert "client-id" not in repr(req)
    assert "client-secret" not in repr(req)


def test_build_request_accepts_fifth_position_alpha_krx_code():
    mod = _adapter_module()
    adapter = mod.NaverAdapter("client-id", "client-secret")
    params = dict(_query().params)
    params.update(
        stock_code="0126Z0",
        stock_name="삼성에피스홀딩스",
        query="0126Z0",
    )
    req = adapter.build_request(_query(params=params), NOW)
    assert req.params["query"] == "0126Z0"


def test_parse_response_filters_irrelevant_docs_and_returns_news_evidence_draft():
    mod = _adapter_module()
    adapter = mod.NaverAdapter("client-id", "client-secret")
    drafts = adapter.parse_response(_raw_success(), _query())
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.source_type == "news"
    assert draft.span_scope == "headline_snippet"
    assert draft.source_url == "https://www.yna.co.kr/view/AKR123"
    assert "<b>" not in draft.raw_span
    assert len(draft.raw_span) <= 500
    assert draft.normalized_value["stock_code"] == "005930"
    assert draft.normalized_value["query"] == "005930"
    assert draft.normalized_value["attribution_reason"].startswith("matched:")


def test_parse_response_rejects_request_lineage_mismatch():
    mod = _adapter_module()
    adapter = mod.NaverAdapter("client-id", "client-secret")
    raw = _raw_success()
    raw["_meta"]["request_query"] = "삼성전자"
    with pytest.raises(ValueError, match="lineage"):
        adapter.parse_response(raw, _query())


def test_classify_error_maps_rate_limit_and_retry_after_header():
    mod = _adapter_module()
    adapter = mod.NaverAdapter("client-id", "client-secret")
    raw = {
        "_meta": {"http_status": 429, "headers": {"retry-after": "2"}},
        "body": {"errorCode": "429"},
    }
    assert adapter.classify_error(raw) == (ReasonCode.RATE_LIMIT, True)
    hint = adapter.rate_limit_hint(raw)
    assert hint is not None
    assert hint.provider == "naver"
    assert hint.retry_after_ms == 2000
    assert hint.source == "header"


@pytest.mark.asyncio
async def test_acall_normalizes_timeout_to_provider_execution_error():
    mod = _adapter_module()

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        adapter = mod.NaverAdapter("client-id", "client-secret", client=client)
        req = adapter.build_request(_query(), NOW)
        with pytest.raises(ProviderExecutionError) as captured:
            await adapter.acall(req)
        assert captured.value.reason_code is ReasonCode.UPSTREAM_TIMEOUT
        assert captured.value.retryable is True
    finally:
        await client.aclose()
