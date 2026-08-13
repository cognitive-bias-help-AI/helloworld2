"""구조화 View를 Draft schema로 호출하는 모델 경계."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from app.schemas.frozen import Usage


@runtime_checkable
class ModelGateway(Protocol):
    async def invoke(
        self,
        slot: Literal["SMALL", "MID", "LARGE"],
        prompt_version: str,
        input_view: BaseModel,
        output_schema: type[BaseModel],
    ) -> tuple[BaseModel, Usage]: ...
