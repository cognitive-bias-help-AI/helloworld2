"""MLAPI ModelGateway configuration boundary.

The deployment contract currently establishes endpoint routing and Bearer
authentication only.  Request and response codecs intentionally remain blocked
until authoritative Luna/Terra/Sol examples are available.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlparse

import httpx
import openai
from pydantic import BaseModel, ValidationError

from app.contexts.budget import ctx_chars
from app.models.registry import USD_KRW
from app.prompts.registry import system_for
from app.schemas.frozen import ModelSpec, Usage

Slot = Literal["SMALL", "MID", "LARGE"]

MLAPI_MODEL_BY_SLOT: Final[dict[Slot, str]] = {
    "SMALL": "gpt-5.6-luna",
    "MID": "gpt-5.6-terra",
    "LARGE": "gpt-5.6-sol",
}
_SLOTS: Final[frozenset[str]] = frozenset(MLAPI_MODEL_BY_SLOT)
_PRICE_USD_BY_SLOT: Final[dict[str, tuple[float, float]]] = {
    "SMALL": (0.20, 1.20),
    "MID": (2.00, 12.00),
    "LARGE": (5.00, 30.00),
}
_MAX_COMPLETION_TOKENS: Final[dict[str, int]] = {
    "SMALL": 4_000,
    "MID": 8_000,
    "LARGE": 16_000,
}
_EFFORT_BY_NODE: Final[dict[str, str]] = {
    "n1": "none",
    "n3": "low",
    "n7": "low",
    "n8": "high",
    "n9": "medium",
    "n10": "low",
    "n11": "low",
}


class MlApiGatewayError(RuntimeError):
    """Normalized provider failure without leaking response bodies or credentials."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"MLAPI gateway failure: {code}")


class MlApiOutputUnusable(RuntimeError):
    """The provider response did not contain the requested typed Draft."""


class MlApiRefusal(RuntimeError):
    """The provider refused to produce the requested Draft."""


class MlApiUsageUnusable(RuntimeError):
    """Provider usage was present but malformed or internally inconsistent."""


@dataclass(frozen=True)
class MlApiEndpoint:
    url: str
    api_key: str = field(repr=False)
    model_label: str = ""

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MLAPI endpoint URL must be an absolute HTTP(S) URL")
        if not self.api_key.strip():
            raise ValueError("MLAPI endpoint API key must be non-blank")
        if not self.model_label.strip():
            raise ValueError("MLAPI endpoint model label must be non-blank")


def normalize_base_url(url: str) -> str:
    normalized = url.rstrip("/")
    return normalized if normalized.endswith("/v1") else f"{normalized}/v1"


class MlApiModelGateway:
    """Slot router over one injected shared HTTP client.

    The gateway does not own or close the injected client.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        endpoints: Mapping[str, MlApiEndpoint],
        _client_factory: Callable[..., Any] = openai.AsyncOpenAI,
    ) -> None:
        endpoint_map = dict(endpoints)
        if set(endpoint_map) != _SLOTS:
            raise ValueError("MLAPI endpoints must contain exactly SMALL, MID, LARGE")
        self._client = client
        self._endpoints = endpoint_map
        self._sdk_clients = {
            slot: _client_factory(
                base_url=normalize_base_url(endpoint.url),
                api_key=endpoint.api_key,
                http_client=client,
                max_retries=0,
            )
            for slot, endpoint in endpoint_map.items()
        }

    def endpoint_for(self, slot: Slot) -> MlApiEndpoint:
        return self._endpoints[slot]

    def headers_for(self, slot: Slot) -> dict[str, str]:
        endpoint = self.endpoint_for(slot)
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "Authorization": f"Bearer {endpoint.api_key}",
        }

    def model_spec_for(self, slot: Slot) -> ModelSpec:
        endpoint = self.endpoint_for(slot)
        input_usd, output_usd = _PRICE_USD_BY_SLOT[slot]
        return ModelSpec(
            slot=slot,
            model_id=endpoint.model_label,
            base_url=normalize_base_url(endpoint.url),
            reasoning_effort=None,
            price_in_krw_per_1m=int(input_usd * USD_KRW),
            price_cached_in_krw_per_1m=None,
            price_out_krw_per_1m=int(output_usd * USD_KRW),
        )

    async def invoke(
        self,
        slot: Slot,
        prompt_version: str,
        input_view: BaseModel,
        output_schema: type[BaseModel],
    ) -> tuple[BaseModel, Usage]:
        if not isinstance(input_view, BaseModel):
            raise TypeError("input_view must be a BaseModel")
        if not (isinstance(output_schema, type) and issubclass(output_schema, BaseModel)):
            raise TypeError("output_schema must be a BaseModel subclass")

        endpoint = self.endpoint_for(slot)
        node = prompt_version.split("/", 1)[0]
        try:
            effort = _EFFORT_BY_NODE[node]
        except KeyError as exc:
            raise ValueError(f"no MLAPI reasoning policy for {prompt_version}") from exc

        request = {
            "model": endpoint.model_label,
            "messages": [
                {"role": "system", "content": system_for(prompt_version)},
                {"role": "user", "content": _payload(input_view)},
            ],
            "response_format": output_schema,
            "reasoning_effort": effort,
            "max_completion_tokens": _MAX_COMPLETION_TOKENS[slot],
        }
        try:
            response = await self._sdk_clients[slot].chat.completions.parse(**request)
        except openai.BadRequestError as exc:
            raise MlApiGatewayError("invalid_request") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise MlApiGatewayError("authentication") from exc
        except openai.RateLimitError as exc:
            raise MlApiGatewayError("rate_limit") from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise MlApiGatewayError("connectivity") from exc
        except openai.APIStatusError as exc:
            code = "upstream" if exc.status_code >= 500 else "provider"
            raise MlApiGatewayError(code) from exc
        except openai.APIError as exc:
            raise MlApiGatewayError("provider") from exc

        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise MlApiOutputUnusable(f"{prompt_version}: response has no choices")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise MlApiOutputUnusable(f"{prompt_version}: response has no message")
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise MlApiRefusal(f"{prompt_version}: model refused the request")
        parsed = getattr(message, "parsed", None)
        if parsed is None or not isinstance(parsed, output_schema):
            raise MlApiOutputUnusable(
                f"{prompt_version}: response did not contain {output_schema.__name__}"
            )
        return parsed, _usage(slot, getattr(response, "usage", None), input_view)


def _payload(input_view: BaseModel) -> str:
    return input_view.model_dump_json(exclude_none=True, indent=None)


def _usage(slot: Slot, raw: Any, input_view: BaseModel) -> Usage:
    if raw is None:
        raise MlApiUsageUnusable("MLAPI response did not contain usage")
    prompt_tokens = _required_nonneg(raw, "prompt_tokens")
    completion_tokens = _required_nonneg(raw, "completion_tokens")
    cached = getattr(raw, "cached_prompt_tokens", None)
    if cached is None:
        details = getattr(raw, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0)
    if not isinstance(cached, int) or isinstance(cached, bool) or cached < 0:
        raise MlApiUsageUnusable("MLAPI cached token usage is invalid")
    try:
        return Usage(
            model_slot=slot,
            prompt_tokens=prompt_tokens,
            cached_input_tokens=cached,
            cache_write_tokens=0,
            output_tokens=completion_tokens,
            ctx_chars=ctx_chars(input_view),
        )
    except ValidationError as exc:
        raise MlApiUsageUnusable("MLAPI usage violates the frozen Usage contract") from exc


def _required_nonneg(raw: Any, name: str) -> int:
    value = getattr(raw, name, None)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MlApiUsageUnusable(f"MLAPI {name} is invalid")
    return value


__all__ = [
    "MLAPI_MODEL_BY_SLOT",
    "MlApiEndpoint",
    "MlApiGatewayError",
    "MlApiModelGateway",
    "MlApiOutputUnusable",
    "MlApiRefusal",
    "MlApiUsageUnusable",
    "normalize_base_url",
]
