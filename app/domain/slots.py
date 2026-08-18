"""Hybrid Adaptive Intake의 8개 Core Slot Registry."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.frozen import NonBlankStr, SlotId


class PreferredInput(StrEnum):
    STRUCTURED = "STRUCTURED"
    FREE_TEXT = "FREE_TEXT"
    HYBRID = "HYBRID"


class AskPolicy(StrEnum):
    ALWAYS_IF_MISSING = "ALWAYS_IF_MISSING"
    CONDITIONAL = "CONDITIONAL"
    USUALLY_SKIP = "USUALLY_SKIP"
    ONCE_RECOMMENDED = "ONCE_RECOMMENDED"


class EvidencePolicy(StrEnum):
    NONE = "NONE"
    CLAIM_DEPENDENT = "CLAIM_DEPENDENT"
    SYSTEM_OPPOSING_SEARCH = "SYSTEM_OPPOSING_SEARCH"


class SlotDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    slot_id: SlotId
    code: NonBlankStr
    label: NonBlankStr
    description: NonBlankStr
    preferred_input: PreferredInput
    required: bool
    blocking: bool
    ask_policy: AskPolicy
    evidence_policy: EvidencePolicy
    allow_llm_extraction: bool
    default_verifiable: bool
    value_shape: Literal["enum", "text", "categories"]
    allowed_values: tuple[str, ...] | None = None


SLOT_REGISTRY: tuple[SlotDefinition, ...] = (
    SlotDefinition(
        slot_id=1,
        code="decision_action",
        label="Decision Action",
        description="사용자가 검토 중인 투자 행동",
        preferred_input=PreferredInput.STRUCTURED,
        required=True,
        blocking=True,
        ask_policy=AskPolicy.ALWAYS_IF_MISSING,
        evidence_policy=EvidencePolicy.NONE,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="enum",
        allowed_values=("CONSIDER_ENTRY", "HOLD", "CONSIDER_EXIT", "WAIT"),
    ),
    SlotDefinition(
        slot_id=2,
        code="holding_state",
        label="Holding State",
        description="현재 종목 보유 여부",
        preferred_input=PreferredInput.STRUCTURED,
        required=True,
        blocking=True,
        ask_policy=AskPolicy.ALWAYS_IF_MISSING,
        evidence_policy=EvidencePolicy.NONE,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="enum",
        allowed_values=("HOLDING", "NOT_HOLDING"),
    ),
    SlotDefinition(
        slot_id=3,
        code="time_horizon",
        label="Time Horizon",
        description="사용자가 고려하는 투자 판단 기간",
        preferred_input=PreferredInput.STRUCTURED,
        required=True,
        blocking=False,
        ask_policy=AskPolicy.CONDITIONAL,
        evidence_policy=EvidencePolicy.NONE,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="enum",
        allowed_values=("SHORT", "MEDIUM", "LONG", "UNDECIDED"),
    ),
    SlotDefinition(
        slot_id=4,
        code="primary_reasons",
        label="Primary Reasons",
        description="투자 판단의 직접 이유",
        preferred_input=PreferredInput.FREE_TEXT,
        required=True,
        blocking=True,
        ask_policy=AskPolicy.ALWAYS_IF_MISSING,
        evidence_policy=EvidencePolicy.CLAIM_DEPENDENT,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="text",
    ),
    SlotDefinition(
        slot_id=5,
        code="expected_outcome",
        label="Expected Outcome",
        description="판단 이유로부터 기대하는 결과",
        preferred_input=PreferredInput.FREE_TEXT,
        required=False,
        blocking=False,
        ask_policy=AskPolicy.CONDITIONAL,
        evidence_policy=EvidencePolicy.CLAIM_DEPENDENT,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="text",
    ),
    SlotDefinition(
        slot_id=6,
        code="information_checked",
        label="Information Checked",
        description="사용자가 이미 확인했다고 인식하는 정보",
        preferred_input=PreferredInput.HYBRID,
        required=False,
        blocking=False,
        ask_policy=AskPolicy.USUALLY_SKIP,
        evidence_policy=EvidencePolicy.NONE,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="categories",
        allowed_values=(
            "FINANCIALS",
            "DISCLOSURE",
            "NEWS",
            "PRICE_CHART",
            "INDUSTRY",
            "OTHER",
            "NONE_CHECKED",
        ),
    ),
    SlotDefinition(
        slot_id=7,
        code="counter_evidence_concerns",
        label="Counter Evidence / Concerns",
        description="판단과 반대되는 정보 또는 우려",
        preferred_input=PreferredInput.FREE_TEXT,
        required=False,
        blocking=False,
        ask_policy=AskPolicy.USUALLY_SKIP,
        evidence_policy=EvidencePolicy.SYSTEM_OPPOSING_SEARCH,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="text",
    ),
    SlotDefinition(
        slot_id=8,
        code="change_conditions",
        label="Change Conditions",
        description="현재 판단을 다시 검토할 조건",
        preferred_input=PreferredInput.HYBRID,
        required=False,
        blocking=False,
        ask_policy=AskPolicy.ONCE_RECOMMENDED,
        evidence_policy=EvidencePolicy.NONE,
        allow_llm_extraction=True,
        default_verifiable=False,
        value_shape="text",
    ),
)

_SLOTS_BY_ID = {item.slot_id: item for item in SLOT_REGISTRY}


def get_slot_definition(slot_id: int) -> SlotDefinition:
    try:
        return _SLOTS_BY_ID[slot_id]
    except KeyError as exc:
        raise ValueError(f"unknown slot_id: {slot_id}") from exc


def allowed_values_for(slot_id: int) -> tuple[str, ...]:
    return get_slot_definition(slot_id).allowed_values or ()


def validate_slot_value(slot_id: int, value: str | tuple[str, ...]):
    definition = get_slot_definition(slot_id)
    if definition.value_shape == "enum":
        if not isinstance(value, str) or value not in allowed_values_for(slot_id):
            raise ValueError(f"invalid value for slot {slot_id}: {value!r}")
        return value
    if definition.value_shape == "text":
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"slot {slot_id} requires non-blank text")
        return value
    values = (value,) if isinstance(value, str) else value
    if not values or len(values) != len(set(values)):
        raise ValueError(f"slot {slot_id} requires unique categories")
    if any(item not in allowed_values_for(slot_id) for item in values):
        raise ValueError(f"invalid category for slot {slot_id}")
    return value
