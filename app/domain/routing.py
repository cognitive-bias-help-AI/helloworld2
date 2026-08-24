"""Resolved Slot과 Missing/HITL 결과를 분기 의미로 축소하는 순수 정책."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from app.domain.hitl_policy import AskTarget
from app.domain.missing import MissingInformation, MissingKind
from app.domain.slot_resolution import CurrentSlotProjection
from app.domain.slots import SLOT_REGISTRY


class RoutingOutcome(StrEnum):
    BLOCKED = "BLOCKED"
    NEEDS_HITL = "NEEDS_HITL"
    READY_FOR_EVIDENCE = "READY_FOR_EVIDENCE"
    CONTEXT_ONLY = "CONTEXT_ONLY"


def decide_routing(
    projections: Iterable[CurrentSlotProjection],
    missing: Iterable[MissingInformation],
    ask_targets: Iterable[AskTarget],
    *,
    verifiable_claim_count: int,
    hard_blocked: bool,
) -> RoutingOutcome:
    """State/Graph에 의존하지 않고 현재 Phase의 다음 의미를 결정한다."""

    projection_items = tuple(projections)
    expected_slot_ids = tuple(item.slot_id for item in SLOT_REGISTRY)
    actual_slot_ids = tuple(sorted(item.slot_id for item in projection_items))
    if len(projection_items) != len(expected_slot_ids) or actual_slot_ids != expected_slot_ids:
        raise ValueError("routing requires exactly 8 unique Core Slot projections")
    if verifiable_claim_count < 0:
        raise ValueError("verifiable_claim_count must be non-negative")

    missing_items = tuple(missing)
    missing_slot_ids = [item.slot_id for item in missing_items]
    if len(missing_slot_ids) != len(set(missing_slot_ids)):
        raise ValueError("duplicate missing slot_id")
    target_items = tuple(ask_targets)
    target_slot_ids = [item.slot_id for item in target_items]
    if len(target_slot_ids) != len(set(target_slot_ids)):
        raise ValueError("duplicate ask target slot_id")
    if not set(target_slot_ids).issubset(missing_slot_ids):
        raise ValueError("ask target must reference current missing information")

    if hard_blocked:
        return RoutingOutcome.BLOCKED
    if target_items:
        return RoutingOutcome.NEEDS_HITL
    if any(
        item.kind in {MissingKind.AMBIGUOUS, MissingKind.CONFLICT}
        for item in missing_items
    ):
        return RoutingOutcome.BLOCKED
    if verifiable_claim_count:
        return RoutingOutcome.READY_FOR_EVIDENCE
    return RoutingOutcome.CONTEXT_ONLY
