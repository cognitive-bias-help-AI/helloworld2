"""Append-only Slot observations에서 현재 Slot 상태를 계산하는 순수 projection."""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.intake import ResponseState
from app.domain.missing import SlotObservation
from app.domain.slot_context import SlotValueObservation
from app.domain.slots import SLOT_REGISTRY, get_slot_definition
from app.schemas.frozen import ULID, NonBlankStr, SlotId, SourceTrace


class _ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class CurrentSlotStatus(StrEnum):
    RESOLVED = "RESOLVED"
    ABSENT = "ABSENT"
    CONFLICT = "CONFLICT"
    AMBIGUOUS = "AMBIGUOUS"


class ResolutionIssueKind(StrEnum):
    CONFLICT = "CONFLICT"
    AMBIGUOUS = "AMBIGUOUS"


class HydratedSlotObservation(_ProjectionModel):
    """Runtime Loader가 text_ref를 역참조해 만든 일시적인 resolver 입력."""

    observation: SlotValueObservation
    text: NonBlankStr | None = None
    resolves_issue_ids: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def enforce_hydration_and_targeting(self):
        definition = get_slot_definition(self.observation.slot_id)
        requires_text = (
            self.observation.response_state is ResponseState.ANSWERED
            and definition.value_shape == "text"
        )
        if requires_text and self.text is None:
            raise ValueError("answered text observation requires hydrated text")
        if not requires_text and self.text is not None:
            raise ValueError("only answered text observation accepts hydrated text")
        if len(self.resolves_issue_ids) != len(set(self.resolves_issue_ids)):
            raise ValueError("duplicate resolved issue id")
        if self.resolves_issue_ids and self.observation.origin is not SourceTrace.USER_CONFIRMED:
            raise ValueError("only USER_CONFIRMED may target a resolution issue")
        return self


class ResolutionIssue(_ProjectionModel):
    """Conflict/Ambiguity를 재계산 가능한 최소 issue identity."""

    issue_id: NonBlankStr
    kind: ResolutionIssueKind
    slot_ids: tuple[SlotId, ...] = Field(min_length=1)
    observation_ids: tuple[ULID, ...] = ()
    source_key: NonBlankStr | None = None

    @model_validator(mode="after")
    def enforce_canonical_members(self):
        if self.slot_ids != tuple(sorted(set(self.slot_ids))):
            raise ValueError("slot_ids must be sorted and unique")
        if self.observation_ids != tuple(sorted(set(self.observation_ids))):
            raise ValueError("observation_ids must be sorted and unique")
        if self.kind is ResolutionIssueKind.AMBIGUOUS:
            if len(self.slot_ids) < 2 or self.source_key is None:
                raise ValueError("ambiguity requires multiple slots and source_key")
        elif self.source_key is not None:
            raise ValueError("conflict issue cannot carry source_key")
        expected = _issue_id(self.kind, self.slot_ids, self.observation_ids, self.source_key)
        if self.issue_id != expected:
            raise ValueError("deterministic issue_id does not match issue body")
        return self


class CurrentSlotProjection(_ProjectionModel):
    """Canonical history에서 언제든 재계산 가능한 compact current-state view."""

    slot_id: SlotId
    status: CurrentSlotStatus
    observation_ids: tuple[ULID, ...] = ()
    values: tuple[NonBlankStr, ...] = ()
    issue_ids: tuple[NonBlankStr, ...] = ()
    response_state: ResponseState

    @model_validator(mode="after")
    def enforce_status_shape(self):
        if self.observation_ids != tuple(sorted(set(self.observation_ids))):
            raise ValueError("observation_ids must be sorted and unique")
        if self.values != tuple(sorted(set(self.values))):
            raise ValueError("values must be sorted and unique")
        if self.issue_ids != tuple(sorted(set(self.issue_ids))):
            raise ValueError("issue_ids must be sorted and unique")
        if self.status is CurrentSlotStatus.ABSENT:
            if self.values or self.issue_ids or self.response_state is ResponseState.ANSWERED:
                raise ValueError("ABSENT cannot carry values, issues, or ANSWERED state")
        elif self.status is CurrentSlotStatus.RESOLVED:
            if not self.values or self.issue_ids:
                raise ValueError("RESOLVED requires values without issues")
            if self.response_state is not ResponseState.ANSWERED:
                raise ValueError("RESOLVED requires ANSWERED state")
        elif not self.issue_ids:
            raise ValueError("CONFLICT and AMBIGUOUS require issue_ids")
        return self


def _issue_id(
    kind: ResolutionIssueKind,
    slot_ids: tuple[int, ...],
    observation_ids: tuple[str, ...],
    source_key: str | None,
) -> str:
    body = json.dumps(
        {
            "kind": kind.value,
            "observation_ids": observation_ids,
            "slot_ids": slot_ids,
            "source_key": source_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"slot_issue_{sha256(body.encode()).hexdigest()[:24]}"


def build_ambiguity_issue(*, slot_ids: Iterable[int], source_key: str) -> ResolutionIssue:
    """Assembler ambiguity의 raw text 없이 stable source identity만 투영한다."""

    slots = tuple(sorted(set(slot_ids)))
    return ResolutionIssue(
        issue_id=_issue_id(ResolutionIssueKind.AMBIGUOUS, slots, (), source_key),
        kind=ResolutionIssueKind.AMBIGUOUS,
        slot_ids=slots,
        source_key=source_key,
    )


def _conflict_issue(
    slot_id: int, observations: Iterable[HydratedSlotObservation]
) -> ResolutionIssue:
    observation_ids = tuple(sorted(item.observation.observation_id for item in observations))
    slots = (slot_id,)
    return ResolutionIssue(
        issue_id=_issue_id(ResolutionIssueKind.CONFLICT, slots, observation_ids, None),
        kind=ResolutionIssueKind.CONFLICT,
        slot_ids=slots,
        observation_ids=observation_ids,
    )


def _values(items: Iterable[HydratedSlotObservation], *, value_shape: str) -> tuple[str, ...]:
    values: set[str] = set()
    for item in items:
        observation = item.observation
        if observation.response_state is not ResponseState.ANSWERED:
            continue
        if value_shape == "text":
            assert item.text is not None
            values.add(item.text)
        elif value_shape == "categories":
            raw = observation.value
            values.update((raw,) if isinstance(raw, str) else (raw or ()))
        else:
            assert isinstance(observation.value, str)
            values.add(observation.value)
    return tuple(sorted(values))


def _has_value_conflict(value_shape: str, values: tuple[str, ...]) -> bool:
    if value_shape == "enum":
        return len(values) > 1
    if value_shape == "categories":
        return "NONE_CHECKED" in values and len(values) > 1
    return False


def _absent_response_state(
    items: Iterable[HydratedSlotObservation],
) -> ResponseState:
    states = {item.observation.response_state for item in items}
    if ResponseState.USER_DECLINED in states:
        return ResponseState.USER_DECLINED
    if ResponseState.UNDECIDED in states:
        return ResponseState.UNDECIDED
    return ResponseState.UNKNOWN


def resolve_current_slots(
    observations: Iterable[HydratedSlotObservation],
    *,
    issues: Iterable[ResolutionIssue] = (),
) -> tuple[CurrentSlotProjection, ...]:
    """입력 순서와 무관하게 Core Slot 1~8의 현재 projection을 계산한다."""

    items = tuple(observations)
    by_observation_id: dict[str, HydratedSlotObservation] = {}
    for item in items:
        observation_id = item.observation.observation_id
        if observation_id in by_observation_id:
            raise ValueError("duplicate observation_id")
        by_observation_id[observation_id] = item

    explicit_issues = tuple(issues)
    explicit_by_id = {item.issue_id: item for item in explicit_issues}
    if len(explicit_by_id) != len(explicit_issues):
        raise ValueError("duplicate issue_id")

    grouped = {
        definition.slot_id: tuple(
            item for item in items if item.observation.slot_id == definition.slot_id
        )
        for definition in SLOT_REGISTRY
    }

    base_conflicts: dict[int, ResolutionIssue] = {}
    for definition in SLOT_REGISTRY:
        base = tuple(item for item in grouped[definition.slot_id] if not item.resolves_issue_ids)
        values = _values(base, value_shape=definition.value_shape)
        if _has_value_conflict(definition.value_shape, values):
            base_conflicts[definition.slot_id] = _conflict_issue(definition.slot_id, base)

    issue_by_id = {
        **explicit_by_id,
        **{item.issue_id: item for item in base_conflicts.values()},
    }
    targeted = tuple(item for item in items if item.resolves_issue_ids)
    for item in targeted:
        for issue_id in item.resolves_issue_ids:
            issue = issue_by_id.get(issue_id)
            if issue is None:
                raise ValueError(f"unknown resolution issue: {issue_id}")
            if item.observation.slot_id not in issue.slot_ids:
                raise ValueError("resolution observation targets unrelated slot")

    resolved_issue_ids = {issue_id for item in targeted for issue_id in item.resolves_issue_ids}
    result: list[CurrentSlotProjection] = []
    for definition in SLOT_REGISTRY:
        slot_id = definition.slot_id
        slot_items = grouped[slot_id]
        slot_targets = tuple(
            item
            for item in slot_items
            if any(issue_id in issue_by_id for issue_id in item.resolves_issue_ids)
        )
        base_items = tuple(item for item in slot_items if not item.resolves_issue_ids)
        replaced_observation_ids = {
            observation_id
            for target in slot_targets
            for issue_id in target.resolves_issue_ids
            for observation_id in issue_by_id[issue_id].observation_ids
        }
        contributors = (
            tuple(
                item
                for item in base_items
                if item.observation.observation_id not in replaced_observation_ids
            )
            + slot_targets
            if slot_targets
            else base_items
        )
        values = _values(contributors, value_shape=definition.value_shape)
        observation_ids = tuple(sorted(item.observation.observation_id for item in slot_items))

        relevant = [
            issue
            for issue in issue_by_id.values()
            if slot_id in issue.slot_ids and issue.issue_id not in resolved_issue_ids
        ]
        value_conflict = _has_value_conflict(definition.value_shape, values)
        if value_conflict and not any(
            issue.kind is ResolutionIssueKind.CONFLICT for issue in relevant
        ):
            relevant.append(_conflict_issue(slot_id, contributors))

        issue_ids = tuple(sorted(issue.issue_id for issue in relevant))
        if any(issue.kind is ResolutionIssueKind.CONFLICT for issue in relevant):
            status = CurrentSlotStatus.CONFLICT
            response_state = ResponseState.ANSWERED
        elif relevant:
            status = CurrentSlotStatus.AMBIGUOUS
            response_state = ResponseState.UNKNOWN
        elif values:
            status = CurrentSlotStatus.RESOLVED
            response_state = ResponseState.ANSWERED
        else:
            status = CurrentSlotStatus.ABSENT
            response_state = _absent_response_state(slot_items)

        result.append(
            CurrentSlotProjection(
                slot_id=slot_id,
                status=status,
                observation_ids=observation_ids,
                values=values,
                issue_ids=issue_ids,
                response_state=response_state,
            )
        )
    return tuple(result)


def to_missing_observations(
    projections: Iterable[CurrentSlotProjection],
) -> tuple[SlotObservation, ...]:
    """Current projection을 기존 Missing analyzer 입력으로 축소한다."""

    items = sorted(projections, key=lambda item: item.slot_id)
    if len({item.slot_id for item in items}) != len(items):
        raise ValueError("duplicate slot projection")
    result: list[SlotObservation] = []
    for item in items:
        definition = get_slot_definition(item.slot_id)
        if item.status is CurrentSlotStatus.RESOLVED:
            if definition.value_shape == "enum":
                value: str | tuple[str, ...] | None = item.values[0]
            elif definition.value_shape == "categories":
                value = item.values
            else:
                value = "\n".join(item.values)
            result.append(
                SlotObservation(
                    slot_id=item.slot_id,
                    value=value,
                    response_state=ResponseState.ANSWERED,
                )
            )
        else:
            result.append(
                SlotObservation(
                    slot_id=item.slot_id,
                    value=None,
                    response_state=(
                        item.response_state
                        if item.status is CurrentSlotStatus.ABSENT
                        else ResponseState.UNKNOWN
                    ),
                    has_conflict=item.status is CurrentSlotStatus.CONFLICT,
                    is_ambiguous=item.status is CurrentSlotStatus.AMBIGUOUS,
                )
            )
    return tuple(result)
