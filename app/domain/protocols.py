from typing import Protocol, runtime_checkable

from app.schemas.frozen import StockCandidate


@runtime_checkable
class StockResolver(Protocol):
    def resolve(self, text: str, limit: int = 5) -> list[StockCandidate]: ...
