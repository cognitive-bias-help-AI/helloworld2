from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from app.contexts.budget import ctx_chars
from app.models.mlapi_gateway import (
    MlApiEndpoint,
    MlApiGatewayError,
    MlApiModelGateway,
    MlApiOutputUnusable,
    MlApiRefusal,
    MlApiUsageUnusable,
    normalize_base_url,
)
from app.models.registry import USD_KRW
from app.prompts.registry import system_for


class InputView(BaseModel):
    statement: str


class OutputDraft(BaseModel):
    result: str


class UnusedClient:
    async def post(self, *_args, **_kwargs):
        raise AssertionError("HTTP must not be called without an authoritative codec")


class FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    async def parse(self, **request):
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeOpenAI:
    def __init__(self, outcome):
        self.completions = FakeCompletions(outcome)
        self.chat = SimpleNamespace(completions=self.completions)


def _response(parsed=None, *, refusal=None, usage=None):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage
        or SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=10),
        ),
    )


def _gateway(outcomes=None):
    outcomes = outcomes or {slot: _response(OutputDraft(result=slot)) for slot in _endpoints()}
    clients = {}
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        slot = next(
            slot for slot, endpoint in _endpoints().items() if endpoint.api_key == kwargs["api_key"]
        )
        clients[slot] = FakeOpenAI(outcomes[slot])
        return clients[slot]

    gateway = MlApiModelGateway(
        UnusedClient(), endpoints=_endpoints(), _client_factory=factory
    )
    return gateway, clients, factory_calls


def _endpoints() -> dict[str, MlApiEndpoint]:
    return {
        "SMALL": MlApiEndpoint("https://luna.mlapi.run", "luna-secret", "gpt-5.6-luna"),
        "MID": MlApiEndpoint("https://terra.mlapi.run", "terra-secret", "gpt-5.6-terra"),
        "LARGE": MlApiEndpoint("https://sol.mlapi.run", "sol-secret", "gpt-5.6-sol"),
    }


@pytest.mark.parametrize(
    ("slot", "url", "api_key", "label"),
    [
        ("SMALL", "https://luna.mlapi.run", "luna-secret", "gpt-5.6-luna"),
        ("MID", "https://terra.mlapi.run", "terra-secret", "gpt-5.6-terra"),
        ("LARGE", "https://sol.mlapi.run", "sol-secret", "gpt-5.6-sol"),
    ],
)
def test_slot은_독립된_endpoint와_credential로_라우팅된다(slot, url, api_key, label):
    gateway, _, _ = _gateway()

    endpoint = gateway.endpoint_for(slot)

    assert endpoint.url == url
    assert endpoint.api_key == api_key
    assert endpoint.model_label == label


def test_endpoint_repr은_API_key를_노출하지_않는다():
    endpoint = _endpoints()["LARGE"]

    assert "sol-secret" not in repr(endpoint)


@pytest.mark.parametrize(
    ("slot", "input_usd", "output_usd"),
    [("SMALL", 0.20, 1.20), ("MID", 2.00, 12.00), ("LARGE", 5.00, 30.00)],
)
def test_MLAPI_model_spec은_GPT_단가를_사용하고_cached_단가를_발명하지_않는다(
    slot, input_usd, output_usd
):
    gateway, _, _ = _gateway()

    spec = gateway.model_spec_for(slot)

    assert spec.model_id == _endpoints()[slot].model_label
    assert spec.base_url == f"{_endpoints()[slot].url}/v1"
    assert spec.price_in_krw_per_1m == int(input_usd * USD_KRW)
    assert spec.price_out_krw_per_1m == int(output_usd * USD_KRW)
    assert spec.price_cached_in_krw_per_1m is None


@pytest.mark.parametrize("slot", ["SMALL", "MID", "LARGE"])
def test_보장된_HTTP_header는_선택된_slot의_Bearer_key만_사용한다(slot):
    gateway, _, _ = _gateway()

    headers = gateway.headers_for(slot)

    endpoint = _endpoints()[slot]
    assert headers == {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {endpoint.api_key}",
    }
    for other_slot, other in _endpoints().items():
        if other_slot != slot:
            assert other.api_key not in headers["Authorization"]


@pytest.mark.parametrize(
    "endpoints",
    [
        {},
        {"SMALL": MlApiEndpoint("https://luna.mlapi.run", "key", "luna")},
        {**_endpoints(), "OTHER": MlApiEndpoint("https://other.mlapi.run", "key", "other")},
    ],
)
def test_gateway는_SMALL_MID_LARGE_endpoint_정확한_집합만_허용한다(endpoints):
    with pytest.raises(ValueError, match="SMALL.*MID.*LARGE"):
        MlApiModelGateway(UnusedClient(), endpoints=endpoints, _client_factory=lambda **_: None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://mlapi.run/deployment", "https://mlapi.run/deployment/v1"),
        ("https://mlapi.run/deployment/", "https://mlapi.run/deployment/v1"),
        ("https://mlapi.run/deployment/v1", "https://mlapi.run/deployment/v1"),
        ("https://mlapi.run/deployment/v1/", "https://mlapi.run/deployment/v1"),
    ],
)
def test_base_url은_v1을_정확히_한번만_붙인다(raw, expected):
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    ("slot", "prompt_version", "model", "effort", "limit"),
    [
        ("SMALL", "n1/v1", "gpt-5.6-luna", "none", 4_000),
        ("SMALL", "n3/v2", "gpt-5.6-luna", "low", 4_000),
        ("SMALL", "n7/v1", "gpt-5.6-luna", "low", 4_000),
        ("LARGE", "n8/v1", "gpt-5.6-sol", "high", 16_000),
        ("LARGE", "n9/v1", "gpt-5.6-sol", "medium", 16_000),
        ("LARGE", "n10/v1", "gpt-5.6-sol", "low", 16_000),
        ("MID", "n11/v1", "gpt-5.6-terra", "low", 8_000),
    ],
)
@pytest.mark.asyncio
async def test_invoke는_slot별_OpenAI_structured_output_contract를_사용한다(
    slot, prompt_version, model, effort, limit
):
    gateway, clients, factory_calls = _gateway()
    view = InputView(statement="검토")

    parsed, usage = await gateway.invoke(slot, prompt_version, view, OutputDraft)

    assert parsed == OutputDraft(result=slot)
    request = clients[slot].completions.requests[0]
    assert request == {
        "model": model,
        "messages": [
            {"role": "system", "content": system_for(prompt_version)},
            {"role": "user", "content": view.model_dump_json(exclude_none=True, indent=None)},
        ],
        "response_format": OutputDraft,
        "reasoning_effort": effort,
        "max_completion_tokens": limit,
    }
    assert "thinking" not in request
    assert "output_config" not in request
    assert "max_tokens" not in request
    config = next(call for call in factory_calls if call["api_key"] == _endpoints()[slot].api_key)
    assert config["base_url"] == f"{_endpoints()[slot].url}/v1"
    assert usage.model_slot == slot
    assert usage.prompt_tokens == 100
    assert usage.cached_input_tokens == 10
    assert usage.cache_write_tokens == 0
    assert usage.output_tokens == 20
    assert usage.ctx_chars == ctx_chars(view)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (_response(None), MlApiOutputUnusable),
        (_response(InputView(statement="wrong")), MlApiOutputUnusable),
        (_response(OutputDraft(result="ignored"), refusal="refused"), MlApiRefusal),
        (SimpleNamespace(choices=[], usage=None), MlApiOutputUnusable),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_refused_output은_fail_closed한다(response, error):
    gateway, _, _ = _gateway({slot: response for slot in _endpoints()})

    with pytest.raises(error):
        await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)


@pytest.mark.parametrize(
    ("provider_error", "code"),
    [
        (openai.BadRequestError("bad", response=httpx.Response(400, request=httpx.Request("POST", "https://example.com")), body=None), "invalid_request"),
        (openai.AuthenticationError("auth", response=httpx.Response(401, request=httpx.Request("POST", "https://example.com")), body=None), "authentication"),
        (openai.PermissionDeniedError("auth", response=httpx.Response(403, request=httpx.Request("POST", "https://example.com")), body=None), "authentication"),
        (openai.RateLimitError("rate", response=httpx.Response(429, request=httpx.Request("POST", "https://example.com")), body=None), "rate_limit"),
        (openai.APIConnectionError(request=httpx.Request("POST", "https://example.com")), "connectivity"),
        (openai.APITimeoutError(request=httpx.Request("POST", "https://example.com")), "connectivity"),
        (openai.InternalServerError("upstream", response=httpx.Response(500, request=httpx.Request("POST", "https://example.com")), body=None), "upstream"),
    ],
)
@pytest.mark.asyncio
async def test_OpenAI_provider_error를_typed_gateway_error로_정규화한다(provider_error, code):
    gateway, _, _ = _gateway({slot: provider_error for slot in _endpoints()})

    with pytest.raises(MlApiGatewayError) as captured:
        await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)

    assert captured.value.code == code


@pytest.mark.asyncio
async def test_실제_AsyncOpenAI_parse가_MLAPI_wire_contract를_사용한다():
    observed = []

    async def handler(request):
        observed.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-5.6-luna",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"result":"typed"}',
                            "refusal": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "prompt_tokens_details": {"cached_tokens": 2},
                },
            },
        )

    endpoints = _endpoints()
    endpoints["SMALL"] = MlApiEndpoint(
        "https://luna.mlapi.run/deployment", "luna-secret", "gpt-5.6-luna"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = MlApiModelGateway(client, endpoints=endpoints)
        parsed, usage = await gateway.invoke(
            "SMALL", "n1/v1", InputView(statement="검토"), OutputDraft
        )

    assert parsed == OutputDraft(result="typed")
    assert usage.cached_input_tokens == 2
    assert len(observed) == 1
    request = observed[0]
    assert str(request.url) == "https://luna.mlapi.run/deployment/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer luna-secret"
    body = json.loads(request.content)
    assert body["model"] == "gpt-5.6-luna"
    assert body["reasoning_effort"] == "none"
    assert body["max_completion_tokens"] == 4_000
    assert body["response_format"]["type"] == "json_schema"
    assert "thinking" not in body
    assert "output_config" not in body
    assert "max_tokens" not in body


@pytest.mark.parametrize(
    "usage",
    [
        SimpleNamespace(prompt_tokens=-1, completion_tokens=1, prompt_tokens_details=None),
        SimpleNamespace(prompt_tokens="10", completion_tokens=1, prompt_tokens_details=None),
        SimpleNamespace(prompt_tokens=10, completion_tokens=True, prompt_tokens_details=None),
        SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=1,
            prompt_tokens_details=SimpleNamespace(cached_tokens=11),
        ),
        None,
    ],
)
@pytest.mark.asyncio
async def test_malformed_usage는_0으로_발명하지_않고_fail_closed한다(usage):
    response = _response(OutputDraft(result="ok"), usage=usage)
    if usage is None:
        response.usage = None
    gateway, _, _ = _gateway({slot: response for slot in _endpoints()})

    with pytest.raises(MlApiUsageUnusable):
        await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)
