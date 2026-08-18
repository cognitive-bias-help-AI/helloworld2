from app.domain.stock_scope import InstrumentCandidate
from app.schemas.frozen import StockCandidate


class FixtureStockResolver:
    def __init__(
        self,
        rows: dict[str, list[StockCandidate]],
        exact_rows: dict[str, list[InstrumentCandidate]] | None = None,
    ) -> None:
        self.rows = rows
        self.exact_rows = exact_rows or {}
        self.resolve_calls: list[tuple[str, int]] = []
        self.resolve_exact_calls: list[str] = []

    def resolve(self, text: str, limit: int = 5) -> list[StockCandidate]:
        self.resolve_calls.append((text, limit))
        return list(self.rows.get(text, []))[:limit]

    def resolve_exact(self, code: str) -> list[InstrumentCandidate]:
        self.resolve_exact_calls.append(code)
        return list(self.exact_rows.get(code, []))
