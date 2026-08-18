"""Canonical source-level Slot observations and deterministic identity."""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.intake import ResponseState
from app.domain.semantic_source import SemanticTextRef
from app.domain.slots import get_slot_definition, validate_slot_value
from app.schemas.frozen import ULID, SlotId, SourceTrace

SLOT_OBSERVATION_SCHEMA_VERSION = "slot_observation/v1"


class ExtractionMethod(StrEnum):
    DIRECT = "DIRECT"
    LLM = "LLM"


class _SlotContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class _SlotObservationBody(_SlotContextModel):
    schema_version: Literal["slot_observation/v1"] = SLOT_OBSERVATION_SCHEMA_VERSION
    slot_id: SlotId
    response_state: ResponseState
    origin: SourceTrace
    extraction_method: ExtractionMethod
    value: str | tuple[str, ...] | None = None
    text_ref: SemanticTextRef | None = None

    @model_validator(mode="after")
    def enforce_value_and_reference_policy(self):
        definition = get_slot_definition(self.slot_id)
        if self.response_state is ResponseState.ANSWERED:
            if definition.value_shape == "text":
                if self.value is not None:
                    raise ValueError("text slot stores a reference, not a copied value")
                if self.text_ref is None:
                    raise ValueError("text slot requires text_ref")
            else:
                if self.value is None:
                    raise ValueError("ANSWERED requires value")
                validate_slot_value(self.slot_id, self.value)
        elif self.value is not None:
            raise ValueError(f"{self.response_state.value} must not carry value")
        if self.extraction_method is ExtractionMethod.LLM and self.text_ref is None:
            raise ValueError("LLM observation requires text_ref")
        return self


class SlotValueObservation(_SlotObservationBody):
    """Append-only canonical fact; it is not a resolved Slot value."""

    observation_id: ULID


def _canonical_body_json(item: _SlotObservationBody) -> str:
    return json.dumps(
        item.model_dump(mode="json", exclude={"observation_id"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def observation_content_sha256(item: SlotValueObservation) -> str:
    body = _SlotObservationBody.model_validate(
        item.model_dump(exclude={"observation_id"})
    )
    return sha256(_canonical_body_json(body).encode("utf-8")).hexdigest()


def expected_observation_id(run_id: str, item: SlotValueObservation) -> str:
    if not run_id.strip():
        raise ValueError("run_id must be non-blank")
    digest = observation_content_sha256(item)
    seed = f"{run_id}|{digest}"
    return "01" + sha256(seed.encode("utf-8")).hexdigest().upper()[:24]


def build_slot_observation(
    run_id: str,
    *,
    slot_id: int,
    response_state: ResponseState,
    origin: SourceTrace,
    extraction_method: ExtractionMethod,
    value: str | tuple[str, ...] | None,
    text_ref: SemanticTextRef | None,
) -> SlotValueObservation:
    """Validate semantic content, then mint its deterministic canonical ID."""

    if not run_id.strip():
        raise ValueError("run_id must be non-blank")
    body = _SlotObservationBody(
        slot_id=slot_id,
        response_state=response_state,
        origin=origin,
        extraction_method=extraction_method,
        value=value,
        text_ref=text_ref,
    )
    digest = sha256(_canonical_body_json(body).encode("utf-8")).hexdigest()
    seed = f"{run_id}|{digest}"
    observation_id = "01" + sha256(seed.encode("utf-8")).hexdigest().upper()[:24]
    return SlotValueObservation(**body.model_dump(), observation_id=observation_id)
