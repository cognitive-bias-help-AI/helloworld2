"""Semantic unit compatibility and Claim eligibility pure policy."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.slots import get_slot_definition
from app.schemas.frozen import SlotId

MAX_VERIFIABLE_CLAIMS: Final = 8


class SemanticKind(StrEnum):
    EXTERNAL_ASSERTION = "EXTERNAL_ASSERTION"
    EXTERNAL_EXPECTATION = "EXTERNAL_EXPECTATION"
    USER_STATE = "USER_STATE"
    USER_PREFERENCE = "USER_PREFERENCE"
    DECISION_RULE = "DECISION_RULE"
    INFORMATION_CHECKED = "INFORMATION_CHECKED"
    SUBJECTIVE_CONCERN = "SUBJECTIVE_CONCERN"


_EXTERNAL_KINDS = {
    SemanticKind.EXTERNAL_ASSERTION,
    SemanticKind.EXTERNAL_EXPECTATION,
}

_ALLOWED_KINDS: dict[int, tuple[SemanticKind, ...]] = {
    1: (SemanticKind.USER_PREFERENCE,),
    2: (SemanticKind.USER_STATE,),
    3: (SemanticKind.USER_PREFERENCE,),
    4: (
        SemanticKind.USER_PREFERENCE,
        SemanticKind.EXTERNAL_ASSERTION,
        SemanticKind.EXTERNAL_EXPECTATION,
    ),
    5: (
        SemanticKind.USER_PREFERENCE,
        SemanticKind.EXTERNAL_EXPECTATION,
    ),
    6: (SemanticKind.INFORMATION_CHECKED,),
    7: (
        SemanticKind.SUBJECTIVE_CONCERN,
        SemanticKind.EXTERNAL_ASSERTION,
        SemanticKind.EXTERNAL_EXPECTATION,
    ),
    8: (
        SemanticKind.DECISION_RULE,
        SemanticKind.EXTERNAL_ASSERTION,
        SemanticKind.EXTERNAL_EXPECTATION,
    ),
}


class _SemanticPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class ClaimEligibility(_SemanticPolicyModel):
    eligible: bool
    reason: Literal[
        "eligible_external_proposition",
        "context_only",
        "incompatible_slot_kind",
    ]


class ClaimMaterializationCandidate(_SemanticPolicyModel):
    """Eligible Semantic Unit metadata; it is not a canonical Claim."""

    original_index: int = Field(ge=0)
    slot_id: SlotId
    global_span_start: int = Field(ge=0)
    global_span_end: int = Field(gt=0)

    @model_validator(mode="after")
    def enforce_forward_span(self):
        if self.global_span_end <= self.global_span_start:
            raise ValueError("global_span_end must be greater than global_span_start")
        return self


class ClaimMaterializationPlan(_SemanticPolicyModel):
    """Atomic global-capacity decision for one eligible candidate batch."""

    materializable_indices: tuple[int, ...]
    eligible_count: int = Field(ge=0)
    existing_count: int = Field(ge=0)
    capacity: Literal[8] = MAX_VERIFIABLE_CLAIMS
    capacity_exceeded: bool

    @model_validator(mode="after")
    def enforce_atomic_capacity(self):
        expected_exceeded = self.existing_count + self.eligible_count > self.capacity
        if self.capacity_exceeded is not expected_exceeded:
            raise ValueError("capacity_exceeded does not match the global Claim count")
        if self.capacity_exceeded and self.materializable_indices:
            raise ValueError("capacity-exceeded batch cannot select candidates")
        if not self.capacity_exceeded and len(self.materializable_indices) != self.eligible_count:
            raise ValueError("under-capacity batch must select every eligible candidate")
        if len(self.materializable_indices) != len(set(self.materializable_indices)):
            raise ValueError("materializable_indices must be unique")
        return self


def allowed_semantic_kinds(slot_id: int) -> tuple[SemanticKind, ...]:
    """Return the stable allowlist for one registered Core Slot."""

    get_slot_definition(slot_id)
    return _ALLOWED_KINDS[slot_id]


def is_semantic_kind_allowed(slot_id: int, semantic_kind: SemanticKind) -> bool:
    return semantic_kind in allowed_semantic_kinds(slot_id)


def evaluate_claim_eligibility(
    slot_id: int, semantic_kind: SemanticKind
) -> ClaimEligibility:
    """Decide eligibility without constructing a canonical Claim."""

    if not is_semantic_kind_allowed(slot_id, semantic_kind):
        return ClaimEligibility(eligible=False, reason="incompatible_slot_kind")
    if semantic_kind in _EXTERNAL_KINDS:
        return ClaimEligibility(
            eligible=True, reason="eligible_external_proposition"
        )
    return ClaimEligibility(eligible=False, reason="context_only")


def plan_claim_materialization(
    candidates: Iterable[ClaimMaterializationCandidate],
    *,
    existing_count: int,
) -> ClaimMaterializationPlan:
    """Select all eligible candidates or none when the global capacity is exceeded."""

    if existing_count < 0:
        raise ValueError("existing_count must be non-negative")
    items = tuple(candidates)
    original_indices = [item.original_index for item in items]
    if len(original_indices) != len(set(original_indices)):
        raise ValueError("duplicate original index")
    spans = [(item.global_span_start, item.global_span_end) for item in items]
    if len(spans) != len(set(spans)):
        raise ValueError("duplicate global span")

    ordered = sorted(
        items,
        key=lambda item: (
            item.global_span_start,
            item.global_span_end,
            item.slot_id,
            item.original_index,
        ),
    )
    capacity_exceeded = existing_count + len(ordered) > MAX_VERIFIABLE_CLAIMS
    return ClaimMaterializationPlan(
        materializable_indices=(
            ()
            if capacity_exceeded
            else tuple(item.original_index for item in ordered)
        ),
        eligible_count=len(ordered),
        existing_count=existing_count,
        capacity_exceeded=capacity_exceeded,
    )


def validate_locked_slot(expected_slot_id: int, proposed_slot_id: int) -> SlotId:
    """Reject an LLM proposal that moves a structured segment to another Slot."""

    get_slot_definition(expected_slot_id)
    get_slot_definition(proposed_slot_id)
    if expected_slot_id != proposed_slot_id:
        raise ValueError(
            f"locked slot mismatch: expected {expected_slot_id}, got {proposed_slot_id}"
        )
    return expected_slot_id
