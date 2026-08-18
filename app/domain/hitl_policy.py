"""MissingInformation에서 이번 turn AskTarget을 선택하는 순수 정책."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.intake import ResponseState
from app.domain.missing import MissingInformation, MissingKind, MissingReason, RequiredFor
from app.domain.slots import AskPolicy, get_slot_definition
from app.schemas.frozen import SlotId

MAX_ASK_PER_TURN = 2


class _HitlPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class HitlContext(_HitlPolicyModel):
    already_asked_slot_ids: tuple[SlotId, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def enforce_unique_history(self):
        if len(self.already_asked_slot_ids) != len(set(self.already_asked_slot_ids)):
            raise ValueError("duplicate already-asked slot_id")
        return self


class AskTarget(_HitlPolicyModel):
    slot_id: SlotId
    kind: MissingKind
    priority: int = Field(ge=0)
    reason: MissingReason
    required_for: tuple[RequiredFor, ...]


def _selection_rank(item: MissingInformation) -> int:
    definition = get_slot_definition(item.slot_id)
    if item.kind is MissingKind.CONFLICT:
        return 0
    if item.blocking:
        return 1
    if definition.required:
        return 2
    return 3


def select_ask_targets(
    missing: Iterable[MissingInformation],
    context: HitlContext | None = None,
) -> tuple[AskTarget, ...]:
    """Policy filter와 안정 정렬을 적용해 최대 두 AskTarget을 반환한다."""

    policy_context = context or HitlContext()
    already_asked = set(policy_context.already_asked_slot_ids)
    candidates: list[MissingInformation] = []
    seen: set[int] = set()
    for item in missing:
        if item.slot_id in seen:
            raise ValueError("duplicate slot_id in missing information")
        seen.add(item.slot_id)
        definition = get_slot_definition(item.slot_id)
        if not item.askable or definition.ask_policy is AskPolicy.USUALLY_SKIP:
            continue
        if item.slot_id in already_asked:
            continue
        if item.response_state in {
            ResponseState.UNDECIDED,
            ResponseState.USER_DECLINED,
        }:
            continue
        candidates.append(item)

    ordered = sorted(
        candidates,
        key=lambda item: (_selection_rank(item), -item.priority, item.slot_id),
    )[:MAX_ASK_PER_TURN]
    return tuple(
        AskTarget(
            slot_id=item.slot_id,
            kind=item.kind,
            priority=item.priority,
            reason=item.reason,
            required_for=item.required_for,
        )
        for item in ordered
    )

