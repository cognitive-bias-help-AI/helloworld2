from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.contexts.budget import ctx_chars
from app.models.anthropic_gateway import (
    AnthropicModelGateway,
    ModelOutputUnusable,
    ModelRefusal,
)


class InputView(BaseModel):
    statement: str
    omitted: str | None = None


class OutputDraft(BaseModel):
    verdict: str


class OtherDraft(BaseModel):
    value: str


class FakeMessages:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict] = []

    async def parse(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


def response(
    parsed_output: BaseModel | None = None,
    *,
    stop_reason: str = "end_turn",
    stop_details=None,
    usage=None,
):
    return SimpleNamespace(
        parsed_output=parsed_output,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage
        or SimpleNamespace(
            input_tokens=11,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=7,
        ),
    )


def gateway_for(reply=None, *, error: Exception | None = None):
    messages = FakeMessages(reply, error)
    return AnthropicModelGateway(FakeClient(messages)), messages


@pytest.mark.asyncio
async def test_SMALL_request는_Haiku_contract와_JSON_payload를_보존한다(monkeypatch):
    monkeypatch.setattr(
        "app.models.anthropic_gateway.system_for",
        lambda prompt_version: f"system:{prompt_version}",
    )
    gateway, messages = gateway_for(response(OutputDraft(verdict="ok")))

    await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)

    assert messages.requests == [
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 4_000,
            "system": "system:n1/v1",
            "messages": [
                {"role": "user", "content": '{"statement":"검토"}'},
            ],
            "output_format": OutputDraft,
        }
    ]


@pytest.mark.asyncio
async def test_MID_request는_Sonnet_effort와_adaptive_thinking을_보존한다(monkeypatch):
    monkeypatch.setattr(
        "app.models.anthropic_gateway.system_for",
        lambda prompt_version: f"system:{prompt_version}",
    )
    gateway, messages = gateway_for(response(OutputDraft(verdict="ok")))

    await gateway.invoke("MID", "n11/v1", InputView(statement="검토"), OutputDraft)

    assert messages.requests == [
        {
            "model": "claude-sonnet-5",
            "max_tokens": 8_000,
            "system": "system:n11/v1",
            "messages": [
                {"role": "user", "content": '{"statement":"검토"}'},
            ],
            "output_format": OutputDraft,
            "output_config": {"effort": "low"},
            "thinking": {"type": "adaptive"},
        }
    ]


@pytest.mark.asyncio
async def test_LARGE_request는_Opus_node_effort와_adaptive_thinking을_보존한다(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.models.anthropic_gateway.system_for",
        lambda prompt_version: f"system:{prompt_version}",
    )
    gateway, messages = gateway_for(response(OutputDraft(verdict="ok")))

    await gateway.invoke("LARGE", "n9/v1", InputView(statement="검토"), OutputDraft)

    assert messages.requests == [
        {
            "model": "claude-opus-5",
            "max_tokens": 16_000,
            "system": "system:n9/v1",
            "messages": [
                {"role": "user", "content": '{"statement":"검토"}'},
            ],
            "output_format": OutputDraft,
            "output_config": {"effort": "medium"},
            "thinking": {"type": "adaptive"},
        }
    ]


@pytest.mark.asyncio
async def test_valid_response는_structured_output과_cache_usage를_normalize한다():
    view = InputView(statement="검토")
    draft = OutputDraft(verdict="ok")
    usage = SimpleNamespace(
        input_tokens=11,
        cache_read_input_tokens=13,
        cache_creation_input_tokens=17,
        output_tokens=19,
    )
    gateway, _ = gateway_for(response(draft, usage=usage))

    actual, normalized = await gateway.invoke("SMALL", "n1/v1", view, OutputDraft)

    assert actual is draft
    assert normalized.model_slot == "SMALL"
    assert normalized.prompt_tokens == 41
    assert normalized.cached_input_tokens == 13
    assert normalized.cache_write_tokens == 17
    assert normalized.output_tokens == 19
    assert normalized.ctx_chars == ctx_chars(view)


@pytest.mark.asyncio
async def test_refusal은_category와_explanation을_ModelRefusal로_보존한다():
    details = SimpleNamespace(category="safety", explanation="cannot comply")
    gateway, _ = gateway_for(
        response(stop_reason="refusal", stop_details=details)
    )

    with pytest.raises(ModelRefusal) as caught:
        await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)

    assert caught.value.category == "safety"
    assert caught.value.explanation == "cannot comply"


@pytest.mark.asyncio
async def test_parsed_output_None은_unusable로_거부한다():
    gateway, _ = gateway_for(response())

    with pytest.raises(ModelOutputUnusable, match="구조화 출력이 비어 있다"):
        await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)


@pytest.mark.asyncio
async def test_wrong_parsed_output_type은_unusable로_거부한다():
    gateway, _ = gateway_for(response(OtherDraft(value="wrong")))

    with pytest.raises(ModelOutputUnusable, match="OutputDraft 가 아니라 OtherDraft"):
        await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)


@pytest.mark.asyncio
async def test_SDK_exception은_gateway에서_숨기지_않고_전파한다():
    class SDKFailure(RuntimeError):
        pass

    failure = SDKFailure("provider unavailable")
    gateway, _ = gateway_for(error=failure)

    with pytest.raises(SDKFailure) as caught:
        await gateway.invoke("SMALL", "n1/v1", InputView(statement="검토"), OutputDraft)

    assert caught.value is failure
