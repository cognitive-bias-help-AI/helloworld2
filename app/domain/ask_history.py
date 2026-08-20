"""Append-only issue-aware HITL ask-history contract."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.hitl_policy import AskTarget, HitlContext
from app.domain.missing import MissingKind, MissingReason
from app.domain.slot_resolution import ResolutionIssue, build_ambiguity_issue
from app.schemas.frozen import ULID, NonBlankStr, SlotId

ASK_RECORD_SCHEMA_VERSION = "ask_record/v2"


class _AskHistoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class AskRecord(_AskHistoryModel):
    """One emitted ask event; issue detail is retained beyond slot projection."""

    schema_version: Literal["ask_record/v1", "ask_record/v2"] = ASK_RECORD_SCHEMA_VERSION
    ask_id: ULID
    ask_key: NonBlankStr
    slot_id: SlotId
    issue_id: NonBlankStr | None = None
    issue_slot_ids: tuple[SlotId, ...] = ()
    issue_source_key: NonBlankStr | None = None
    kind: MissingKind
    reason: MissingReason
    sequence: int = Field(ge=0)

    @model_validator(mode="after")
    def enforce_versioned_issue_lineage(self):
        if self.schema_version == "ask_record/v1":
            if self.issue_slot_ids or self.issue_source_key is not None:
                raise ValueError("ask_record/v1 cannot carry v2 issue lineage")
            return self
        if self.issue_id is None:
            if self.issue_slot_ids or self.issue_source_key is not None:
                raise ValueError("issue lineage requires issue_id")
            return self
        if self.kind is MissingKind.AMBIGUOUS:
            if not self.issue_slot_ids or self.issue_source_key is None:
                raise ValueError("ambiguity requires complete issue lineage")
            if self.issue_slot_ids != tuple(sorted(set(self.issue_slot_ids))):
                raise ValueError("ambiguity issue_slot_ids must be sorted and unique")
            if self.slot_id not in self.issue_slot_ids:
                raise ValueError("ambiguity issue_slot_ids must contain target slot_id")
            issue = build_ambiguity_issue(
                slot_ids=self.issue_slot_ids, source_key=self.issue_source_key
            )
            if issue.issue_id != self.issue_id:
                raise ValueError("ambiguity issue lineage does not match issue_id")
        elif self.issue_slot_ids or self.issue_source_key is not None:
            raise ValueError("only ambiguity stores additional issue lineage")
        return self


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
    issue_slot_ids: tuple[int, ...] = (),
    issue_source_key: str | None = None,
) -> AskRecord:
    return AskRecord(
        ask_id=expected_ask_id(run_id, ask_key),
        ask_key=ask_key,
        slot_id=target.slot_id,
        issue_id=issue_id,
        issue_slot_ids=issue_slot_ids,
        issue_source_key=issue_source_key,
        kind=target.kind,
        reason=target.reason,
        sequence=sequence,
    )


def reconstruct_ambiguity_issue(record: AskRecord) -> ResolutionIssue:
    """Rebuild a v2 ambiguity projection and verify its stored identity."""

    if record.schema_version != "ask_record/v2" or record.kind is not MissingKind.AMBIGUOUS:
        raise ValueError("AskRecord does not contain reconstructable ambiguity lineage")
    assert record.issue_source_key is not None
    issue = build_ambiguity_issue(
        slot_ids=record.issue_slot_ids, source_key=record.issue_source_key
    )
    if issue.issue_id != record.issue_id:
        raise ValueError("ambiguity issue lineage does not match issue_id")
    return issue


def project_hitl_context(records: list[AskRecord]) -> HitlContext:
    """Project issue-aware history into the current slot-level MVP policy."""

    return HitlContext(
        already_asked_slot_ids=tuple(sorted({item.slot_id for item in records}))
    )
