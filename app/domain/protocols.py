from typing import Protocol, runtime_checkable

from app.domain.stock_scope import InstrumentCandidate
from app.schemas.frozen import StockCandidate


@runtime_checkable
class StockResolver(Protocol):
    def resolve(self, text: str, limit: int = 5) -> list[StockCandidate]: ...

    def resolve_exact(self, code: str) -> list[InstrumentCandidate]: ...
