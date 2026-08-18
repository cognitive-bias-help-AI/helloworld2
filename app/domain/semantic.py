"""Semantic unit compatibility and Claim eligibility pure policy."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.slots import get_slot_definition
from app.schemas.frozen import SlotId


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


class ClaimCardinality(_SemanticPolicyModel):
    eligible_unit_count: int = Field(ge=0)
    ambiguous: bool
    materializable_index: int | None = Field(default=None, ge=0)


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


def evaluate_slot_claim_cardinality(
    slot_id: int, semantic_kinds: Iterable[SemanticKind]
) -> ClaimCardinality:
    """Select zero or one eligible unit index; never choose among multiple units."""

    eligible_indices: list[int] = []
    for index, semantic_kind in enumerate(semantic_kinds):
        result = evaluate_claim_eligibility(slot_id, semantic_kind)
        if result.reason == "incompatible_slot_kind":
            raise ValueError(
                f"incompatible semantic kind for slot {slot_id}: {semantic_kind.value}"
            )
        if result.eligible:
            eligible_indices.append(index)

    count = len(eligible_indices)
    return ClaimCardinality(
        eligible_unit_count=count,
        ambiguous=count > 1,
        materializable_index=eligible_indices[0] if count == 1 else None,
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
