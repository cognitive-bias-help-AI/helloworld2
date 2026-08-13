from app.schemas.frozen import StockCandidate


class FixtureStockResolver:
    def __init__(self, rows: dict[str, list[StockCandidate]]) -> None:
        self.rows = rows

    def resolve(self, text: str, limit: int = 5) -> list[StockCandidate]:
        return list(self.rows.get(text, []))[:limit]
