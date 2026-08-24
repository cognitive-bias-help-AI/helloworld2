"""P0-3 Context payload 예산과 결정론적 Evidence 절단."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

from pydantic import BaseModel

from app.contexts.views import (
    AskBackContext,
    EvidenceIntentView,
    EvidencePacket,
    GuardBatchEnvelope,
    GuardScanView,
    IntegrationView,
    RenderView,
    SemanticExtractionView,
    SlotContext,
    VerifyPacket,
)


@dataclass(frozen=True)
class ContextBudget:
    items: int | None
    chars: int


NODE_BUDGETS: Final[dict[str, ContextBudget]] = {
    "n1": ContextBudget(None, 2000),
    "n3": ContextBudget(8, 6000),
    "n4": ContextBudget(2, 1500),
    "n5": ContextBudget(18, 3000),
    "n7": ContextBudget(12, 4000),
    "n8": ContextBudget(12, 4500),
    "n9": ContextBudget(8, 5000),
    "n10": ContextBudget(8, 3000),
    "n11": ContextBudget(8, 3500),
}

CLAIM_EVIDENCE_LIMIT: Final = 9
STOCK_EVIDENCE_LIMIT: Final = 3
TOTAL_EVIDENCE_LIMIT: Final = 12


def ctx_chars(view: BaseModel) -> int:
    """View payload JSON의 문자 수. 최종 packed prompt 길이는 포함하지 않는다."""

    return len(view.model_dump_json(exclude_none=True))


def ctx_items(view: BaseModel) -> int:
    """각 노드가 반복 처리하는 의미 단위 수를 센다."""

    if isinstance(view, GuardScanView):
        return 0
    if isinstance(view, SlotContext):
        return len(view.slot_definitions)
    if isinstance(view, SemanticExtractionView):
        return len(view.segments)
    if isinstance(view, AskBackContext):
        return len(view.missing_slots)
    if isinstance(view, EvidenceIntentView):
        return len(view.allowed_categories)
    if isinstance(view, EvidencePacket | VerifyPacket):
        return len(view.evidence)
    if isinstance(view, IntegrationView):
        return len(view.evaluations)
    if isinstance(view, GuardBatchEnvelope):
        return len(view.items)
    if isinstance(view, RenderView):
        return len(view.slots)
    raise TypeError(f"지원하지 않는 Context View: {type(view).__name__}")


def validate_context_budget(node: str, view: BaseModel) -> None:
    """모델 호출 전 View의 item/character 예산을 절단 없이 검증한다."""

    try:
        budget = NODE_BUDGETS[node]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 node budget: {node}") from exc

    items = ctx_items(view)
    chars = ctx_chars(view)
    if budget.items is not None and items > budget.items:
        raise ValueError(f"{node} item budget 초과: {items}>{budget.items}")
    if chars > budget.chars:
        raise ValueError(f"{node} char budget 초과: {chars}>{budget.chars}")


def validate_evidence_counts(*, claim_count: int, stock_count: int) -> None:
    """claim-scope 9 + stock-scope 3 = total 12 상한을 한 곳에서 강제한다."""

    if claim_count < 0 or stock_count < 0:
        raise ValueError("Evidence count는 0 이상이어야 함")
    if claim_count > CLAIM_EVIDENCE_LIMIT:
        raise ValueError("claim-scope Evidence는 9건을 초과할 수 없음")
    if stock_count > STOCK_EVIDENCE_LIMIT:
        raise ValueError("stock-scope Evidence는 3건을 초과할 수 없음")
    if claim_count + stock_count > TOTAL_EVIDENCE_LIMIT:
        raise ValueError("Evidence 합계는 12건을 초과할 수 없음")


class _TruncatableEvidence(Protocol):
    as_of: datetime
    evidence_id: str


def truncate[EvidenceT: _TruncatableEvidence](
    items: list[EvidenceT], limit: int
) -> tuple[list[EvidenceT], int]:
    """최오래 1건과 최신 limit-1건을 보존하고 ID순으로 결정화한다."""

    if limit <= 0:
        raise ValueError("limit은 1 이상이어야 함")

    ordered = sorted(items, key=lambda evidence: (evidence.as_of, evidence.evidence_id))
    if len(ordered) <= limit:
        return ordered, 0
    if limit == 1:
        return [ordered[0]], len(ordered) - 1

    kept = [ordered[0], *ordered[-(limit - 1) :]]
    return sorted(kept, key=lambda evidence: evidence.evidence_id), len(ordered) - limit
