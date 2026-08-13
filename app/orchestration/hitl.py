from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.frozen import StockCandidate


class _HitlModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StockChoiceOption(_HitlModel):
    selected_code: str
    display_name: str
    market: Literal["KOSPI", "KOSDAQ"]


class StockChoiceRequest(_HitlModel):
    query: str
    candidates: list[StockChoiceOption]

    @classmethod
    def from_candidates(cls, query: str, candidates: list[StockCandidate]):
        return cls(
            query=query,
            candidates=[
                StockChoiceOption(
                    selected_code=item.code, display_name=item.name, market=item.market
                )
                for item in candidates
            ],
        )


class StockChoiceResume(_HitlModel):
    selected_code: str


def select_stock(
    candidates: list[StockCandidate], resume: StockChoiceResume | None
) -> StockCandidate | None:
    by_code = {item.code: item for item in candidates}
    if len(by_code) != len(candidates):
        raise ValueError("duplicate candidate code")
    if not candidates:
        return None
    if len(candidates) == 1 and resume is None:
        return candidates[0]
    if resume is None:
        raise LookupError("selection required")
    if resume.selected_code not in by_code:
        raise ValueError("selected stock was not offered")
    return by_code[resume.selected_code]
