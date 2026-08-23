"""운영 ModelGateway — Anthropic Messages API 구현.

`MockModelGateway` 와 같은 `ModelGateway` Protocol 을 만족한다. 노드는 슬롯
이름만 알고 어떤 모델이 앉아 있는지 모른다는 원칙(registry.py)은 그대로다.

🔴 이 파일이 지키는 계약

  - **tools / function calling 을 쓰지 않는다** (영구 결정).
    구조화 출력은 `output_config.format` 이고, SDK 의 `messages.parse()` 가
    output_schema 로부터 스키마를 만들고 응답을 검증한다.
  - effort 는 인자로 받지 않는다. `registry.effort_for(slot, prompt_version)` 이
    유일한 결정 지점이다 — 노드마다 값이 갈라지면 "이 판정이 어떤 설정에서
    나왔는가" 를 사후에 추적할 수 없다.
  - Draft 만 돌려준다. canonical ID·lineage·시각은 조립기가 소유한다.

thinking 파라미터를 슬롯별로 다르게 주는 이유는 registry.THINKING_DEFAULT_ON
과 같다. Haiku 4.5 는 adaptive thinking 을 지원하지 않으므로 파라미터를 아예
보내지 않는다. Opus 5 · Sonnet 5 는 생략해도 adaptive 가 켜지지만, 명시해서
"이 호출이 thinking 을 쓴다" 를 코드에서 읽히게 한다.
"""

from __future__ import annotations

from typing import Any, Final, Literal

import anthropic
from pydantic import BaseModel

from app.contexts.budget import ctx_chars
from app.models.registry import MODEL_BY_SLOT, THINKING_DEFAULT_ON, effort_for
from app.prompts.registry import system_for
from app.schemas.frozen import Usage

Slot = Literal["SMALL", "MID", "LARGE"]

# 🔴 슬롯별 max_tokens.
#
#    thinking 토큰과 응답 텍스트의 **합**에 걸리는 상한이다. 여유 없이 잡으면
#    응답이 중간에 잘리고, 잘린 JSON 은 스키마 검증에서 실패해 재시도를 태운다
#    — 즉 너무 낮게 잡으면 오히려 비싸다.
#
#    SMALL 은 thinking 이 없으므로 응답 길이만 보면 된다. n7 은 packet 12건에
#    대한 stance 배열이 전부라 짧다.
#    LARGE 는 adaptive thinking 이 붙는다. n8 이 이 시스템 최난도 추론이므로
#    가장 크게 잡는다.
#
#    [미측정] 실호출 전이라 근거는 스키마 크기 추정뿐이다. Live smoke test 에서
#    usage.output_tokens 분포를 보고 조정한다.
_MAX_TOKENS: Final[dict[str, int]] = {
    "SMALL": 4_000,
    "MID": 8_000,
    "LARGE": 16_000,
}


class ModelRefusal(RuntimeError):
    """모델이 안전상 응답을 거부했다. 재시도로 풀리지 않는다."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        self.category = category
        self.explanation = explanation
        super().__init__(f"model refused: category={category!r}")


class ModelOutputUnusable(RuntimeError):
    """호출은 성공했으나 구조화 출력이 비어 있거나 스키마를 만족하지 못했다."""


class AnthropicModelGateway:
    """`ModelGateway` 의 운영 구현.

    client 를 주입받는 이유: 수명을 소유하지 않기 위해서다. HTTP client 와
    마찬가지로 만든 쪽이 닫는다(composition root).
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        max_tokens: dict[str, int] | None = None,
    ) -> None:
        self._client = client
        self._max_tokens = dict(max_tokens or _MAX_TOKENS)

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

        model_id = MODEL_BY_SLOT[slot]
        effort = effort_for(slot, prompt_version)

        request: dict[str, Any] = {
            "model": model_id,
            "max_tokens": self._max_tokens[slot],
            "system": system_for(prompt_version),
            "messages": [{"role": "user", "content": _payload(input_view)}],
            "output_format": output_schema,
        }
        if effort is not None:
            request["output_config"] = {"effort": effort}
        if THINKING_DEFAULT_ON[model_id]:
            request["thinking"] = {"type": "adaptive"}

        response = await self._client.messages.parse(**request)

        if response.stop_reason == "refusal":
            details = response.stop_details
            raise ModelRefusal(
                getattr(details, "category", None),
                getattr(details, "explanation", None),
            )

        parsed = response.parsed_output
        if parsed is None:
            raise ModelOutputUnusable(
                f"{prompt_version}: 구조화 출력이 비어 있다 "
                f"(stop_reason={response.stop_reason!r})"
            )
        if not isinstance(parsed, output_schema):
            raise ModelOutputUnusable(
                f"{prompt_version}: {output_schema.__name__} 가 아니라 "
                f"{type(parsed).__name__} 가 돌아왔다"
            )

        return parsed, _usage(slot, response.usage, input_view)


def _payload(input_view: BaseModel) -> str:
    """View 를 모델에 보이는 형태로 직렬화한다.

    `exclude_none=True` 인 이유: budget.ctx_chars 가 같은 조건으로 문자를 세기
    때문이다. 여기서 다르게 직렬화하면 예산 검증이 실제 전송량과 어긋난다.
    """
    return input_view.model_dump_json(exclude_none=True, indent=None)


def _usage(slot: str, raw: Any, input_view: BaseModel) -> Usage:
    """provider usage 를 frozen Usage 로 정규화한다.

    🔴 prompt_tokens 는 **정규화된 입력 총량**이다(frozen.Usage docstring).
       Anthropic 의 `input_tokens` 는 캐시에 걸리지 않은 몫만 세므로 그대로
       쓰면 cached_input_tokens > prompt_tokens 가 되어 스키마가 거부한다.
       셋을 더한 값이 "이번 호출이 읽은 입력 전체" 다.
    """
    uncached = _nonneg(getattr(raw, "input_tokens", 0))
    cache_read = _nonneg(getattr(raw, "cache_read_input_tokens", 0))
    cache_write = _nonneg(getattr(raw, "cache_creation_input_tokens", 0))
    return Usage(
        model_slot=slot,
        prompt_tokens=uncached + cache_read + cache_write,
        cached_input_tokens=cache_read,
        cache_write_tokens=cache_write,
        output_tokens=_nonneg(getattr(raw, "output_tokens", 0)),
        ctx_chars=ctx_chars(input_view),
    )


def _nonneg(value: Any) -> int:
    """usage 필드는 None 으로 올 수 있다. 비용 회계가 None 에서 멈추면 안 된다."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


__all__ = [
    "AnthropicModelGateway",
    "ModelOutputUnusable",
    "ModelRefusal",
]
