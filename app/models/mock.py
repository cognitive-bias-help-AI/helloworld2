"""Deterministic ModelGateway test double with a closed output allowlist."""

from typing import Literal

from pydantic import BaseModel

from app.contexts.budget import ctx_chars
from app.orchestration.drafts import (
    AskBackDraft,
    FindingDraft,
    GuardScanResult,
    GuardVerdictDraft,
    RenderDraft,
    SemanticExtractionDraft,
    SlotExtractionDraft,
)
from app.schemas.frozen import ClaimEvaluationDraft, ClaimStanceDraft, Usage

_ALLOWED_OUTPUTS: frozenset[type[BaseModel]] = frozenset(
    {
        GuardScanResult,
        SlotExtractionDraft,
        SemanticExtractionDraft,
        AskBackDraft,
        ClaimStanceDraft,
        ClaimEvaluationDraft,
        FindingDraft,
        GuardVerdictDraft,
        RenderDraft,
    }
)


class MockModelGateway:
    def __init__(self, responses: dict[type[BaseModel], BaseModel]) -> None:
        self._responses = dict(responses)

    async def invoke(
        self,
        slot: Literal["SMALL", "MID", "LARGE"],
        prompt_version: str,
        input_view: BaseModel,
        output_schema: type[BaseModel],
    ) -> tuple[BaseModel, Usage]:
        del prompt_version
        if not isinstance(input_view, BaseModel):
            raise TypeError("input_view는 BaseModel이어야 함")
        if output_schema not in _ALLOWED_OUTPUTS:
            raise ValueError("허용되지 않은 output_schema")
        response = self._responses.get(output_schema)
        if response is None:
            raise ValueError("output_schema fixture가 없음")
        if not isinstance(response, output_schema):
            raise TypeError("output_schema와 fixture 타입이 일치하지 않음")
        usage = Usage(
            model_slot=slot,
            prompt_tokens=0,
            cached_input_tokens=0,
            cache_write_tokens=0,
            output_tokens=0,
            ctx_chars=ctx_chars(input_view),
        )
        return response.model_copy(deep=True), usage
