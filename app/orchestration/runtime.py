"""Run-scoped dependencies and raw-input transport for the S0 graph."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.domain.intake import HybridIntake
from app.domain.protocols import StockResolver
from app.gateway.admission import ProviderAdmissionController
from app.gateway.protocols import ProviderAdapter
from app.models.protocols import ModelGateway
from app.orchestration.reporting import RenderCandidateStore
from app.store.protocols import EvidenceStore, ReviewStore


@dataclass(frozen=True)
class ReviewRequestContext:
    """Ephemeral transport. It is never a State or Store body."""

    raw_text: str | None = None
    intake: HybridIntake | None = None

    def __post_init__(self) -> None:
        if (self.raw_text is None) == (self.intake is None):
            raise ValueError("exactly one of raw_text or intake is required")


Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True)
class RuntimeDeps:
    review_store: ReviewStore
    evidence_store: EvidenceStore
    provider_admission: ProviderAdmissionController
    model_gateway: ModelGateway
    stock_resolver: StockResolver
    adapters: Mapping[str, ProviderAdapter]
    clock: Clock
    id_factory: IdFactory
    render_candidates: RenderCandidateStore
