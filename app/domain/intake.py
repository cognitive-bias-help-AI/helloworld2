"""Hybrid Adaptive Intake의 Runtime 비연결 Domain contract."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.frozen import KRXCode, NonBlankStr, SlotId, SourceTrace


class _IntakeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class IntakeMode(StrEnum):
    """입력 시작 방식 metadata. 이후 허용 입력을 제한하지 않는다."""

    SURVEY_FIRST = "SURVEY_FIRST"
    CHAT_FIRST = "CHAT_FIRST"
    HYBRID = "HYBRID"


class ResponseState(StrEnum):
    """Provenance와 분리된 사용자 응답 상태."""

    ANSWERED = "answered"
    UNKNOWN = "unknown"
    UNDECIDED = "undecided"
    USER_DECLINED = "user_declined"


class TargetSecurityInput(_IntakeModel):
    """아직 canonical stock으로 확정되지 않은 explicit 종목 후보."""

    selected_code: KRXCode | None = None
    name: NonBlankStr | None = None
    market: Literal["KOSPI", "KOSDAQ"] | None = None
    source: SourceTrace


class StructuredAnswer(_IntakeModel):
    """Core Slot 하나에 대한 구조화 응답. Claim으로 변환하지 않는다."""

    slot_id: SlotId
    value: str | tuple[str, ...] | None = None
    source: SourceTrace
    response_state: ResponseState

    @model_validator(mode="after")
    def enforce_response_state(self):
        if self.response_state is ResponseState.ANSWERED and self.value is None:
            raise ValueError("ANSWERED requires value")
        if self.response_state is not ResponseState.ANSWERED and self.value is not None:
            raise ValueError(f"{self.response_state.value} must not carry value")
        if self.response_state is ResponseState.ANSWERED:
            from app.domain.slots import validate_slot_value

            validate_slot_value(self.slot_id, self.value)
        return self


class FreeTextInput(_IntakeModel):
    """n0 이전 raw user input을 허용하는 자유 입력."""

    text: NonBlankStr
    source: SourceTrace


class HybridIntake(_IntakeModel):
    """Survey, Chat, Hybrid가 공유하는 Phase A 입력 envelope."""

    schema_version: Literal["hybrid_intake/v1"]
    mode: IntakeMode
    target: TargetSecurityInput | None = None
    structured: tuple[StructuredAnswer, ...] = Field(default_factory=tuple)
    free_text: tuple[FreeTextInput, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def enforce_unique_structured_slots(self):
        slot_ids = [item.slot_id for item in self.structured]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("duplicate slot_id in structured answers")
        return self
