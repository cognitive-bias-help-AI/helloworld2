"""Pure SemanticExtractionDraft to canonical observation/Claim assembler."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.intake import ResponseState, StructuredAnswer
from app.domain.semantic import (
    ClaimMaterializationCandidate,
    ClaimMaterializationPlan,
    SemanticKind,
    evaluate_claim_eligibility,
    plan_claim_materialization,
)
from app.domain.semantic_source import (
    SEMANTIC_PROJECTION_VERSION,
    SemanticTextRef,
    SemanticTextSegment,
    build_semantic_anchor,
)
from app.domain.slot_context import (
    ExtractionMethod,
    SlotValueObservation,
    build_slot_observation,
)
from app.domain.slots import get_slot_definition, validate_slot_value
from app.orchestration.drafts import SemanticExtractionDraft, SemanticUnitDraft
from app.schemas.frozen import Claim, SourceTrace

_USER_TEXT_ORIGINS = {
    SourceTrace.SURVEY,
    SourceTrace.CHAT_EXPLICIT,
    SourceTrace.USER_CONFIRMED,
}
_EXTERNAL_KINDS = {
    SemanticKind.EXTERNAL_ASSERTION,
    SemanticKind.EXTERNAL_EXPECTATION,
}


class SemanticAssemblyStatus(StrEnum):
    SUCCESS = "SUCCESS"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    AMBIGUOUS = "AMBIGUOUS"


class _AssemblyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class SemanticAmbiguity(_AssemblyModel):
    segment_id: str
    span_offset: tuple[int, int]
    slot_ids: tuple[int, ...]

    @model_validator(mode="after")
    def require_multiple_slots(self):
        if len(self.slot_ids) < 2 or len(self.slot_ids) != len(set(self.slot_ids)):
            raise ValueError("semantic ambiguity requires multiple unique slots")
        return self


class SemanticAssemblyResult(_AssemblyModel):
    status: SemanticAssemblyStatus
    observations: tuple[SlotValueObservation, ...] = ()
    claims: tuple[Claim, ...] = ()
    capacity_plan: ClaimMaterializationPlan | None = None
    ambiguities: tuple[SemanticAmbiguity, ...] = ()

    @model_validator(mode="after")
    def enforce_status_contract(self):
        if self.status is SemanticAssemblyStatus.SUCCESS:
            if self.ambiguities:
                raise ValueError("SUCCESS cannot carry ambiguities")
            if self.capacity_plan is None or self.capacity_plan.capacity_exceeded:
                raise ValueError("SUCCESS requires an under-capacity plan")
        elif self.status is SemanticAssemblyStatus.CAPACITY_EXCEEDED:
            if self.observations or self.claims or self.ambiguities:
                raise ValueError("capacity failure cannot carry canonical outputs")
            if self.capacity_plan is None or not self.capacity_plan.capacity_exceeded:
                raise ValueError("capacity failure requires an exceeded plan")
        elif self.status is SemanticAssemblyStatus.AMBIGUOUS:
            if self.observations or self.claims or self.capacity_plan:
                raise ValueError("ambiguity cannot carry canonical outputs")
            if not self.ambiguities:
                raise ValueError("AMBIGUOUS requires ambiguity records")
        return self


SemanticErrorFamily = Literal["contract", "span", "schema"]


class SemanticAssemblyError(ValueError):
    """Typed fail-closed error raised before canonical output is returned."""

    def __init__(
        self,
        category: str,
        *,
        family: SemanticErrorFamily = "contract",
        retryable: bool = False,
        detail: str = "",
    ) -> None:
        self.category = category
        self.family = family
        self.retryable = retryable
        super().__init__(detail or category)


@dataclass(frozen=True)
class _ObservationSpec:
    slot_id: int
    response_state: ResponseState
    origin: SourceTrace
    extraction_method: ExtractionMethod
    value: str | tuple[str, ...] | None
    text_ref: SemanticTextRef | None


@dataclass(frozen=True)
class _ValidatedUnit:
    unit: SemanticUnitDraft
    segment: SemanticTextSegment
    text_ref: SemanticTextRef
    global_start: int
    global_end: int


def _error(
    category: str,
    *,
    family: SemanticErrorFamily = "contract",
    retryable: bool = False,
    detail: str = "",
) -> SemanticAssemblyError:
    return SemanticAssemblyError(
        category, family=family, retryable=retryable, detail=detail
    )


def _prepare_direct_observations(
    answers: tuple[StructuredAnswer, ...],
    segment_by_id: dict[str, SemanticTextSegment],
) -> tuple[_ObservationSpec, ...]:
    slot_ids = [item.slot_id for item in answers]
    if len(slot_ids) != len(set(slot_ids)):
        raise _error("duplicate_structured_slot", family="schema")
    answer_by_slot = {item.slot_id: item for item in answers}
    specs: list[_ObservationSpec] = []

    for item in sorted(answers, key=lambda value: value.slot_id):
        try:
            definition = get_slot_definition(item.slot_id)
        except ValueError as exc:
            raise _error("unknown_structured_slot", family="schema") from exc

        value = item.value
        text_ref = None
        if item.response_state is ResponseState.ANSWERED:
            if value is None:
                raise _error("answered_without_value", family="schema")
            try:
                validate_slot_value(item.slot_id, value)
            except ValueError as exc:
                raise _error("invalid_structured_value", family="schema") from exc
            if definition.value_shape == "text":
                segment_id = f"structured:{item.slot_id}"
                source = segment_by_id.get(segment_id)
                if source is None:
                    raise _error("missing_structured_text_segment")
                if (
                    source.locked_slot_id != item.slot_id
                    or source.origin is not item.source
                    or source.text != value
                ):
                    raise _error("structured_text_segment_mismatch")
                text_ref = SemanticTextRef(
                    segment_id=segment_id,
                    local_start=0,
                    local_end=len(source.text),
                )
                value = None
        elif value is not None:
            raise _error("non_answered_with_value", family="schema")

        specs.append(
            _ObservationSpec(
                slot_id=item.slot_id,
                response_state=item.response_state,
                origin=item.source,
                extraction_method=ExtractionMethod.DIRECT,
                value=value,
                text_ref=text_ref,
            )
        )

    for source in segment_by_id.values():
        if not source.segment_id.startswith("structured:"):
            continue
        try:
            slot_id = int(source.segment_id.split(":", 1)[1])
        except ValueError as exc:
            raise _error("invalid_structured_segment_id") from exc
        item = answer_by_slot.get(slot_id)
        if (
            item is None
            or item.response_state is not ResponseState.ANSWERED
            or get_slot_definition(slot_id).value_shape != "text"
            or item.value != source.text
            or item.source is not source.origin
            or source.locked_slot_id != slot_id
        ):
            raise _error("orphan_structured_text_segment")
    return tuple(specs)


def _validate_proposed_value(unit: SemanticUnitDraft) -> None:
    definition = get_slot_definition(unit.slot_id)
    if definition.value_shape in {"enum", "categories"}:
        if unit.proposed_value is None:
            raise _error("missing_proposed_value", retryable=True)
        try:
            validate_slot_value(unit.slot_id, unit.proposed_value)
        except ValueError as exc:
            raise _error("invalid_proposed_value", retryable=True) from exc
    elif unit.proposed_value is not None:
        raise _error("unexpected_proposed_value", retryable=True)


def _unit_sort_key(item: _ValidatedUnit) -> tuple:
    proposed = item.unit.proposed_value
    proposed_key = (proposed,) if isinstance(proposed, str) else (proposed or ())
    return (
        item.global_start,
        item.global_end,
        item.unit.slot_id,
        item.unit.semantic_kind.value,
        item.segment.segment_id,
        item.unit.text_span,
        proposed_key,
        item.unit.normalized_proposition or "",
    )


def _claim_id(
    run_id: str,
    projection_version: str,
    item: _ValidatedUnit,
) -> str:
    body = {
        "normalized_proposition": item.unit.text_span,
        "origin": item.segment.origin.value,
        "projection_version": projection_version,
        "segment_id": item.segment.segment_id,
        "slot_id": item.unit.slot_id,
        "span_offset": [item.global_start, item.global_end],
        "user_text_span": item.unit.text_span,
        "verifiable": True,
    }
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = sha256(encoded.encode("utf-8")).hexdigest()
    return "01" + sha256(f"{run_id}|{digest}".encode()).hexdigest().upper()[:24]


def _materialize_observation(
    run_id: str, spec: _ObservationSpec
) -> SlotValueObservation:
    return build_slot_observation(
        run_id,
        slot_id=spec.slot_id,
        response_state=spec.response_state,
        origin=spec.origin,
        extraction_method=spec.extraction_method,
        value=spec.value,
        text_ref=spec.text_ref,
    )


def assemble_semantic_extraction(
    draft: SemanticExtractionDraft | None,
    *,
    run_id: str,
    projection_version: str,
    segments: Iterable[SemanticTextSegment],
    structured_answers: Iterable[StructuredAnswer],
    existing_verifiable_claim_count: int,
    run_started_at: datetime,
) -> SemanticAssemblyResult:
    """Validate the complete batch, then atomically return canonical bodies."""

    if not run_id.strip():
        raise _error("blank_run_id", family="schema")
    if projection_version != SEMANTIC_PROJECTION_VERSION:
        raise _error("unknown_projection_version")
    if run_started_at.utcoffset() is None:
        raise _error("naive_run_started_at", family="schema")
    if existing_verifiable_claim_count < 0:
        raise _error("negative_existing_claim_count", family="schema")

    sources = tuple(segments)
    answers = tuple(structured_answers)
    try:
        anchor = build_semantic_anchor(sources)
    except ValueError as exc:
        raise _error("invalid_semantic_segments") from exc
    segment_by_id = {item.segment_id: item for item in sources}
    direct_specs = _prepare_direct_observations(answers, segment_by_id)

    if sources and draft is None:
        raise _error("missing_semantic_draft")
    units = () if draft is None else tuple(draft.units)

    identities: dict[tuple, tuple] = {}
    span_slots: dict[tuple[str, int, int], set[int]] = {}
    external_by_span_slot: dict[tuple[str, int, int, int], SemanticKind] = {}
    validated: list[_ValidatedUnit] = []

    for item in units:
        source = segment_by_id.get(item.segment_id)
        if source is None:
            raise _error("unknown_segment", retryable=True)
        try:
            get_slot_definition(item.slot_id)
        except ValueError as exc:
            raise _error("unknown_slot", family="schema", retryable=True) from exc
        if source.locked_slot_id is not None and source.locked_slot_id != item.slot_id:
            raise _error("locked_slot_mismatch", retryable=True)

        start, end = item.span_offset
        if start < 0 or end <= start or end > len(source.text):
            raise _error("invalid_span_bounds", family="span", retryable=True)
        if source.text[start:end] != item.text_span:
            raise _error("span_mismatch", family="span", retryable=True)
        global_start = source.anchor_start + start
        global_end = source.anchor_start + end
        if anchor[global_start:global_end] != item.text_span:
            raise _error("global_span_mismatch", family="span", retryable=True)

        eligibility = evaluate_claim_eligibility(item.slot_id, item.semantic_kind)
        if eligibility.reason == "incompatible_slot_kind":
            raise _error("incompatible_slot_kind", retryable=True)
        _validate_proposed_value(item)
        if item.semantic_kind in _EXTERNAL_KINDS:
            if (
                not isinstance(item.normalized_proposition, str)
                or not item.normalized_proposition.strip()
            ):
                raise _error("blank_external_proposition", family="schema", retryable=True)
            if source.origin not in _USER_TEXT_ORIGINS:
                raise _error("invalid_claim_origin", retryable=True)

        identity = (
            item.segment_id,
            item.slot_id,
            start,
            end,
            item.semantic_kind.value,
        )
        payload = (
            item.text_span,
            item.proposed_value,
            item.normalized_proposition,
        )
        previous = identities.get(identity)
        if previous is not None:
            category = "duplicate_unit" if previous == payload else "inconsistent_unit"
            raise _error(category, retryable=True)
        identities[identity] = payload

        span_key = (item.segment_id, start, end)
        span_slots.setdefault(span_key, set()).add(item.slot_id)
        if item.semantic_kind in _EXTERNAL_KINDS:
            external_key = (*span_key, item.slot_id)
            if external_key in external_by_span_slot:
                raise _error("same_span_external_claim", retryable=True)
            external_by_span_slot[external_key] = item.semantic_kind

        validated.append(
            _ValidatedUnit(
                unit=item,
                segment=source,
                text_ref=SemanticTextRef(
                    segment_id=item.segment_id,
                    local_start=start,
                    local_end=end,
                ),
                global_start=global_start,
                global_end=global_end,
            )
        )

    ambiguities = tuple(
        SemanticAmbiguity(
            segment_id=segment_id,
            span_offset=(start, end),
            slot_ids=tuple(sorted(slot_ids)),
        )
        for (segment_id, start, end), slot_ids in sorted(span_slots.items())
        if len(slot_ids) > 1
    )
    if ambiguities:
        return SemanticAssemblyResult(
            status=SemanticAssemblyStatus.AMBIGUOUS,
            ambiguities=ambiguities,
        )

    ordered_units = tuple(sorted(validated, key=_unit_sort_key))
    external_units = tuple(
        item for item in ordered_units if item.unit.semantic_kind in _EXTERNAL_KINDS
    )
    candidates = tuple(
        ClaimMaterializationCandidate(
            original_index=index,
            slot_id=item.unit.slot_id,
            global_span_start=item.global_start,
            global_span_end=item.global_end,
        )
        for index, item in enumerate(external_units)
    )
    try:
        plan = plan_claim_materialization(
            candidates, existing_count=existing_verifiable_claim_count
        )
    except ValueError as exc:
        raise _error("invalid_claim_plan") from exc
    if plan.capacity_exceeded:
        return SemanticAssemblyResult(
            status=SemanticAssemblyStatus.CAPACITY_EXCEEDED,
            capacity_plan=plan,
        )

    observation_specs = [*direct_specs]
    observation_specs.extend(
        _ObservationSpec(
            slot_id=item.unit.slot_id,
            response_state=ResponseState.ANSWERED,
            origin=item.segment.origin,
            extraction_method=ExtractionMethod.LLM,
            value=item.unit.proposed_value,
            text_ref=item.text_ref,
        )
        for item in ordered_units
    )
    observations_by_id: dict[str, SlotValueObservation] = {}
    for spec in observation_specs:
        observation = _materialize_observation(run_id, spec)
        observations_by_id.setdefault(observation.observation_id, observation)

    external_by_index = dict(enumerate(external_units))
    claims = tuple(
        Claim(
            claim_id=_claim_id(run_id, projection_version, external_by_index[index]),
            slot_id=external_by_index[index].unit.slot_id,
            user_text_span=external_by_index[index].unit.text_span,
            span_offset=(
                external_by_index[index].global_start,
                external_by_index[index].global_end,
            ),
            normalized_proposition=(
                external_by_index[index].unit.normalized_proposition or ""
            ),
            verifiable=True,
            origin=external_by_index[index].segment.origin,
            created_at=run_started_at,
        )
        for index in plan.materializable_indices
    )
    return SemanticAssemblyResult(
        status=SemanticAssemblyStatus.SUCCESS,
        observations=tuple(observations_by_id.values()),
        claims=claims,
        capacity_plan=plan,
    )
