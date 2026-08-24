from __future__ import annotations

import pytest

from providers.krx.client import KrxClient, KrxContractError


class Response:
    def __init__(self, body, *, status_code=200):
        self.body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self.body, BaseException):
            raise self.body
        return self.body


class HttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_KRX_transport는_canonical_key를_AUTH_KEY_header로만_전달한다():
    http = HttpClient([Response({"OutBlock_1": [{"ISU_SRT_CD": "005930"}]})])
    client = KrxClient(http, api_key="canonical-secret")

    rows = await client.fetch_basic_info("KOSPI", "20260821")

    assert rows == ({"ISU_SRT_CD": "005930"},)
    url, request = http.calls[0]
    assert url.endswith("/svc/apis/sto/stk_isu_base_info")
    assert request == {
        "headers": {"AUTH_KEY": "canonical-secret", "accept": "application/json"},
        "params": {"basDd": "20260821"},
    }
    assert "KRX_API_KEY" not in request["headers"]


@pytest.mark.asyncio
async def test_KOSDAQ은_별도_공식_endpoint를_사용한다():
    http = HttpClient([Response({"OutBlock_1": []})])
    client = KrxClient(http, api_key="secret")

    await client.fetch_basic_info("KOSDAQ", "20260821")

    assert http.calls[0][0].endswith("/svc/apis/sto/ksq_isu_base_info")


@pytest.mark.parametrize(
    "body",
    [[], {}, {"OutBlock_1": "wrong"}, {"OutBlock_1": ["wrong"]}],
)
@pytest.mark.asyncio
async def test_malformed_KRX_response는_contract_error다(body):
    client = KrxClient(HttpClient([Response(body)]), api_key="secret")

    with pytest.raises(KrxContractError):
        await client.fetch_basic_info("KOSPI", "20260821")


def test_blank_KRX_API_KEY와_invalid_date_market은_호출전에_거부한다():
    with pytest.raises(ValueError, match="KRX_API_KEY"):
        KrxClient(HttpClient([]), api_key="")

    client = KrxClient(HttpClient([]), api_key="secret")
    with pytest.raises(ValueError, match="market"):
        client.request_for("KONEX", "20260821")
    with pytest.raises(ValueError, match="basDd"):
        client.request_for("KOSPI", "2026-08-21")
