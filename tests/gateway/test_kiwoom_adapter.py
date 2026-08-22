from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.gateway.adapters.kiwoom import KiwoomAdapter
from app.gateway.execution import ProviderExecutionError
from app.schemas.frozen import Query, ReasonCode
from providers.kiwoom.core import (
    AdapterResult,
    Environment,
    ErrorCategory,
    KiwoomCredentials,
    KiwoomRequest,
    ProviderError,
    RateLimitInfo,
    ResultStatus,
)
from providers.kiwoom.core import KiwoomAdapter as CoreKiwoomAdapter

NOW = datetime(2026, 8, 21, tzinfo=UTC)
QUERY_ID = "01K5ZTQ9X7WPCVN2M4H8JRAB3D"


def query(endpoint: str, params: dict) -> Query:
    return Query(
        query_id=QUERY_ID,
        scope="stock",
        intent="context",
        provider="kiwoom",
        endpoint=endpoint,
        params=params,
        created_at=NOW,
    )


class RecordingCore:
    def __init__(self, result: AdapterResult):
        self.result = result
        self.requests: list[KiwoomRequest] = []

    async def request(self, request: KiwoomRequest) -> AdapterResult:
        self.requests.append(request)
        return self.result


class TimeoutCore:
    async def request(self, request: KiwoomRequest) -> AdapterResult:
        del request
        raise httpx.ReadTimeout("timed out")


class FakeResponse:
    def __init__(self, body: dict, *, status_code: int = 200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._body


class SequenceHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, *, headers, json):
        self.calls.append((url, headers, json))
        return self.responses.pop(0)


def success_result(tr: str, data) -> AdapterResult:
    return AdapterResult(
        status=ResultStatus.SUCCESS,
        provider="kiwoom",
        tr=tr,
        request_params={},
        data=data,
    )


@pytest.mark.parametrize(
    ("endpoint", "params", "tr", "core_params"),
    [
        (
            "current_quote",
            {"stock_code": "005930"},
            "ka10007",
            {"stk_cd": "005930"},
        ),
        (
            "daily_price_history",
            {
                "stock_code": "005930",
                "base_date": "20260821",
                "adjusted_price": True,
            },
            "ka10081",
            {"stk_cd": "005930", "base_dt": "20260821", "upd_stkpc_tp": "1"},
        ),
        (
            "investor_flow",
            {
                "stock_code": "005930",
                "date": "20260821",
                "measure": "quantity",
                "trade_kind": "net_buy",
                "unit": "thousand_shares",
            },
            "ka10059",
            {
                "dt": "20260821",
                "stk_cd": "005930",
                "amt_qty_tp": "2",
                "trde_tp": "0",
                "unit_tp": "1000",
            },
        ),
    ],
)
def test_build_request는_semantic_endpoint를_supported_TR로_mapping한다(
    endpoint, params, tr, core_params
):
    adapter = KiwoomAdapter(
        RecordingCore(success_result(tr, {})), environment=Environment.MOCK
    )

    request = adapter.build_request(query(endpoint, params), NOW)

    assert request.provider == "kiwoom"
    assert request.endpoint == tr
    assert request.params == core_params


@pytest.mark.asyncio
async def test_acall은_Main_Request를_Core_Request로_번역한다():
    core = RecordingCore(
        success_result("ka10007", {"stock_code": "005930", "current_price": 71800})
    )
    adapter = KiwoomAdapter(core, environment=Environment.MOCK)
    request = adapter.build_request(
        query("current_quote", {"stock_code": "005930"}), NOW
    )

    raw = await adapter.acall(request)

    assert core.requests == [
        KiwoomRequest(
            tr="ka10007", params={"stk_cd": "005930"}, environment=Environment.MOCK
        )
    ]
    assert raw["status"] == "success"
    assert raw["data"]["current_price"] == 71800


@pytest.mark.asyncio
async def test_Main_Adapter는_실제_Core의_인증_단일호출_정규화를_사용한다():
    http = SequenceHttp(
        FakeResponse({"return_code": 0, "token": "token", "expires_dt": "20991231235959"}),
        FakeResponse(
            {
                "return_code": 0,
                "stk_cd": "005930",
                "cur_prc": "-71800",
                "trde_qty": "1234",
            },
            headers={"api-id": "ka10007", "cont-yn": "N"},
        ),
    )
    core = CoreKiwoomAdapter(http, KiwoomCredentials("app", "secret"))
    adapter = KiwoomAdapter(core, environment=Environment.MOCK)
    q = query("current_quote", {"stock_code": "005930"})

    raw = await adapter.acall(adapter.build_request(q, NOW))
    drafts = adapter.parse_response(raw, q)

    assert len(http.calls) == 2
    assert drafts[0].normalized_value == {
        "kind": "current_quote",
        "stock_code": "005930",
        "current_price": 71800,
        "volume": 1234,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("return_code", "expected"),
    [
        (1700, ReasonCode.RATE_LIMIT),
        (1701, ReasonCode.CONTRACT_VIOLATION),
        (1702, ReasonCode.CONTRACT_VIOLATION),
    ],
)
async def test_Wrapper는_verified_1700만_RATE_LIMIT으로_분류한다(return_code, expected):
    http = SequenceHttp(
        FakeResponse({"return_code": 0, "token": "token", "expires_dt": "20991231235959"}),
        FakeResponse(
            {"return_code": return_code, "return_msg": "limit"},
            headers={"Retry-After": "2"},
        ),
    )
    core = CoreKiwoomAdapter(http, KiwoomCredentials("app", "secret"))
    adapter = KiwoomAdapter(core, environment=Environment.MOCK)
    q = query("current_quote", {"stock_code": "005930"})

    raw = await adapter.acall(adapter.build_request(q, NOW))

    assert adapter.classify_error(raw) == (expected, expected is ReasonCode.RATE_LIMIT)
    hint = adapter.rate_limit_hint(raw)
    assert (hint.retry_after_ms if hint is not None else None) == (
        2000 if expected is ReasonCode.RATE_LIMIT else None
    )


@pytest.mark.parametrize(
    ("endpoint", "params", "raw", "source_ref", "kind"),
    [
        (
            "current_quote",
            {"stock_code": "005930"},
            {
                "status": "success",
                "provider": "kiwoom",
                "tr": "ka10007",
                "request_params": {"stk_cd": "005930"},
                "data": {"stock_code": "005930", "current_price": 71800},
                "has_more": False,
                "next_key": None,
                "raw_reference": {},
                "error": None,
            },
            "ka10007:005930",
            "current_quote",
        ),
        (
            "daily_price_history",
            {
                "stock_code": "005930",
                "base_date": "20260821",
                "adjusted_price": True,
            },
            {
                "status": "success",
                "provider": "kiwoom",
                "tr": "ka10081",
                "request_params": {},
                "data": [
                    {
                        "stock_code": "005930",
                        "date": "2026-08-21",
                        "adjusted_price": True,
                        "close": 71800,
                        "volume": 1234,
                    }
                ],
                "has_more": False,
                "next_key": None,
                "raw_reference": {},
                "error": None,
            },
            "ka10081:005930:2026-08-21:adjusted",
            "daily_price",
        ),
        (
            "investor_flow",
            {
                "stock_code": "005930",
                "date": "20260821",
                "measure": "quantity",
                "trade_kind": "net_buy",
                "unit": "shares",
            },
            {
                "status": "success",
                "provider": "kiwoom",
                "tr": "ka10059",
                "request_params": {},
                "data": [
                    {
                        "stock_code": "005930",
                        "date": "2026-08-21",
                        "measure": "quantity",
                        "trade_kind": "net_buy",
                        "unit": "shares",
                        "foreigner": 100,
                    }
                ],
                "has_more": False,
                "next_key": None,
                "raw_reference": {},
                "error": None,
            },
            "ka10059:005930:2026-08-21:quantity:net_buy:shares",
            "investor_flow",
        ),
    ],
)
def test_parse_response는_Core_data를_quote_EvidenceDraft로_mapping한다(
    endpoint, params, raw, source_ref, kind
):
    adapter = KiwoomAdapter(
        RecordingCore(success_result(raw["tr"], raw["data"])),
        environment=Environment.MOCK,
    )

    drafts = adapter.parse_response(raw, query(endpoint, params))

    assert len(drafts) == 1
    assert drafts[0].source_type == "quote"
    assert drafts[0].source_ref == source_ref
    assert drafts[0].normalized_value["kind"] == kind
    assert len(drafts[0].raw_span) <= 500


def test_ka10099와_unsupported_semantic_endpoint는_fail_closed한다():
    adapter = KiwoomAdapter(
        RecordingCore(success_result("ka10099", [])), environment=Environment.MOCK
    )

    with pytest.raises(ValueError, match="unsupported Kiwoom endpoint"):
        adapter.build_request(query("stock_list", {"market": "0"}), NOW)


@pytest.mark.parametrize(
    ("category", "reason"),
    [
        (ErrorCategory.RATE_LIMIT, ReasonCode.RATE_LIMIT),
        (ErrorCategory.AUTH, ReasonCode.AUTH_FAILED),
        (ErrorCategory.IP_MISMATCH, ReasonCode.IP_MISMATCH),
        (ErrorCategory.HTTP_SERVER, ReasonCode.UPSTREAM_5XX),
        (ErrorCategory.NETWORK, ReasonCode.UPSTREAM_TIMEOUT),
    ],
)
def test_Core_error_category를_Main_ReasonCode로_mapping한다(category, reason):
    adapter = KiwoomAdapter(
        RecordingCore(success_result("ka10007", {})), environment=Environment.MOCK
    )
    raw = adapter.encode_result(
        AdapterResult(
            status=ResultStatus.ERROR,
            provider="kiwoom",
            tr="ka10007",
            request_params={"stk_cd": "005930"},
            error=ProviderError(
                category=category,
                message="provider error",
                retryable=True,
                code=1700 if category is ErrorCategory.RATE_LIMIT else None,
                limit_info=(
                    RateLimitInfo(retry_after_seconds=3)
                    if category is ErrorCategory.RATE_LIMIT
                    else None
                ),
            ),
        )
    )

    assert adapter.classify_error(raw) == (reason, True)
    hint = adapter.rate_limit_hint(raw)
    if category is ErrorCategory.RATE_LIMIT:
        assert hint is not None and hint.retry_after_ms == 3000
    else:
        assert hint is None


@pytest.mark.asyncio
async def test_Kiwoom_timeout은_typed_execution_error로_normalize된다():
    adapter = KiwoomAdapter(TimeoutCore(), environment=Environment.MOCK)
    request = adapter.build_request(query("current_quote", {"stock_code": "005930"}), NOW)

    with pytest.raises(ProviderExecutionError) as caught:
        await adapter.acall(request)

    assert caught.value.reason_code is ReasonCode.UPSTREAM_TIMEOUT
    assert caught.value.retryable is True
