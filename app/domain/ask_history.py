"""Append-only issue-aware HITL ask-history contract."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.hitl_policy import AskTarget, HitlContext
from app.domain.missing import MissingKind, MissingReason
from app.schemas.frozen import ULID, NonBlankStr, SlotId

ASK_RECORD_SCHEMA_VERSION = "ask_record/v1"


class _AskHistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class AskRecord(_AskHistoryModel):
    """One emitted ask event; issue detail is retained beyond slot projection."""

    schema_version: Literal["ask_record/v1"] = ASK_RECORD_SCHEMA_VERSION
    ask_id: ULID
    ask_key: NonBlankStr
    slot_id: SlotId
    issue_id: NonBlankStr | None = None
    kind: MissingKind
    reason: MissingReason
    sequence: int = Field(ge=0)


def expected_ask_id(run_id: str, ask_key: str) -> str:
    if not run_id.strip():
        raise ValueError("run_id must be non-blank")
    if not ask_key.strip():
        raise ValueError("ask_key must be non-blank")
    seed = f"ask_record|{run_id}|{ask_key}"
    return "01" + sha256(seed.encode("utf-8")).hexdigest().upper()[:24]


def build_ask_record(
    run_id: str,
    *,
    ask_key: str,
    target: AskTarget,
    sequence: int,
    issue_id: str | None = None,
) -> AskRecord:
    return AskRecord(
        ask_id=expected_ask_id(run_id, ask_key),
        ask_key=ask_key,
        slot_id=target.slot_id,
        issue_id=issue_id,
        kind=target.kind,
        reason=target.reason,
        sequence=sequence,
    )


def project_hitl_context(records: list[AskRecord]) -> HitlContext:
    """Project issue-aware history into the current slot-level MVP policy."""

    return HitlContext(
        already_asked_slot_ids=tuple(sorted({item.slot_id for item in records}))
    )
