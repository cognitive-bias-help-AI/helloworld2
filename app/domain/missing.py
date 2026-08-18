"""Core Slot 상태에서 MissingInformation을 계산하는 순수 Domain logic."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.intake import ResponseState
from app.domain.slots import AskPolicy, get_slot_definition, validate_slot_value
from app.schemas.frozen import SlotId


class _MissingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class MissingKind(StrEnum):
    ABSENT = "ABSENT"
    PARTIAL = "PARTIAL"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"


class MissingReason(StrEnum):
    ACTION_REQUIRED = "ACTION_REQUIRED"
    HOLDING_STATE_REQUIRED = "HOLDING_STATE_REQUIRED"
    HORIZON_UNKNOWN = "HORIZON_UNKNOWN"
    PRIMARY_REASON_REQUIRED = "PRIMARY_REASON_REQUIRED"
    EXPECTED_OUTCOME_MISSING = "EXPECTED_OUTCOME_MISSING"
    INFORMATION_CHECKED_MISSING = "INFORMATION_CHECKED_MISSING"
    COUNTER_EVIDENCE_MISSING = "COUNTER_EVIDENCE_MISSING"
    CHANGE_CONDITION_MISSING = "CHANGE_CONDITION_MISSING"
    SLOT_PARTIAL = "SLOT_PARTIAL"
    SLOT_AMBIGUOUS = "SLOT_AMBIGUOUS"
    SLOT_CONFLICT = "SLOT_CONFLICT"


class RequiredFor(StrEnum):
    DECISION_CONTEXT = "DECISION_CONTEXT"
    EVIDENCE_PLANNING = "EVIDENCE_PLANNING"
    REPORT_EXPLANATION = "REPORT_EXPLANATION"
    CONFLICT_RESOLUTION = "CONFLICT_RESOLUTION"


class SlotObservation(_MissingModel):
    """Runtime State와 독립적인 현재 Slot 관찰값."""

    slot_id: SlotId
    value: str | tuple[str, ...] | None = None
    response_state: ResponseState
    is_partial: bool = False
    is_ambiguous: bool = False
    has_conflict: bool = False

    @model_validator(mode="after")
    def enforce_value_state(self):
        if self.response_state is ResponseState.ANSWERED and self.value is None:
            raise ValueError("ANSWERED requires value")
        if self.response_state is not ResponseState.ANSWERED and self.value is not None:
            raise ValueError(f"{self.response_state.value} must not carry value")
        if self.response_state is ResponseState.ANSWERED:
            validate_slot_value(self.slot_id, self.value)
        return self


class MissingInformation(_MissingModel):
    slot_id: SlotId
    kind: MissingKind
    blocking: bool
    askable: bool
    priority: int = Field(ge=0)
    reason: MissingReason
    required_for: tuple[RequiredFor, ...]
    response_state: ResponseState


_ABSENT_REASONS = {
    1: MissingReason.ACTION_REQUIRED,
    2: MissingReason.HOLDING_STATE_REQUIRED,
    3: MissingReason.HORIZON_UNKNOWN,
    4: MissingReason.PRIMARY_REASON_REQUIRED,
    5: MissingReason.EXPECTED_OUTCOME_MISSING,
    6: MissingReason.INFORMATION_CHECKED_MISSING,
    7: MissingReason.COUNTER_EVIDENCE_MISSING,
    8: MissingReason.CHANGE_CONDITION_MISSING,
}

_PRIORITIES = {1: 100, 2: 90, 3: 60, 4: 95, 5: 40, 6: 10, 7: 10, 8: 50}

_REQUIRED_FOR = {
    1: (RequiredFor.DECISION_CONTEXT,),
    2: (RequiredFor.DECISION_CONTEXT,),
    3: (RequiredFor.DECISION_CONTEXT,),
    4: (RequiredFor.EVIDENCE_PLANNING, RequiredFor.REPORT_EXPLANATION),
    5: (RequiredFor.REPORT_EXPLANATION,),
    6: (RequiredFor.REPORT_EXPLANATION,),
    7: (RequiredFor.EVIDENCE_PLANNING,),
    8: (RequiredFor.DECISION_CONTEXT,),
}


def _missing_kind(item: SlotObservation) -> MissingKind | None:
    if item.has_conflict:
        return MissingKind.CONFLICT
    if item.is_ambiguous:
        return MissingKind.AMBIGUOUS
    if item.is_partial:
        return MissingKind.PARTIAL
    if item.value is None:
        return MissingKind.ABSENT
    return None


def _reason(slot_id: int, kind: MissingKind) -> MissingReason:
    if kind is MissingKind.CONFLICT:
        return MissingReason.SLOT_CONFLICT
    if kind is MissingKind.AMBIGUOUS:
        return MissingReason.SLOT_AMBIGUOUS
    if kind is MissingKind.PARTIAL:
        return MissingReason.SLOT_PARTIAL
    return _ABSENT_REASONS[slot_id]


def analyze_missing(
    observations: Iterable[SlotObservation],
) -> tuple[MissingInformation, ...]:
    """입력 순서와 무관하게 slot_id 순 MissingInformation을 반환한다."""

    items = list(observations)
    slot_ids = [item.slot_id for item in items]
    if len(slot_ids) != len(set(slot_ids)):
        raise ValueError("duplicate slot_id in observations")

    result: list[MissingInformation] = []
    for item in sorted(items, key=lambda value: value.slot_id):
        kind = _missing_kind(item)
        if kind is None:
            continue
        definition = get_slot_definition(item.slot_id)
        result.append(
            MissingInformation(
                slot_id=item.slot_id,
                kind=kind,
                blocking=definition.blocking,
                askable=(
                    kind is MissingKind.CONFLICT
                    or definition.ask_policy is not AskPolicy.USUALLY_SKIP
                ),
                priority=120 if kind is MissingKind.CONFLICT else _PRIORITIES[item.slot_id],
                reason=_reason(item.slot_id, kind),
                required_for=(
                    (RequiredFor.CONFLICT_RESOLUTION,)
                    if kind is MissingKind.CONFLICT
                    else _REQUIRED_FOR[item.slot_id]
                ),
                response_state=item.response_state,
            )
        )
    return tuple(result)

