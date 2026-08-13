import pytest

from app.domain.protocols import StockResolver
from app.orchestration.hitl import StockChoiceRequest, StockChoiceResume, select_stock
from app.schemas.frozen import StockCandidate
from tests.s0.fakes import FixtureStockResolver


def candidate(code: str, name: str) -> StockCandidate:
    return StockCandidate(code=code, name=name, market="KOSPI", match_kind="exact_name", score=1.0)


def test_fixture_resolver_is_deterministic_and_satisfies_port():
    resolver = FixtureStockResolver({"삼성": [candidate("005930", "삼성전자")]})
    assert isinstance(resolver, StockResolver)
    assert resolver.resolve("삼성") == resolver.resolve("삼성")


def test_stock_choice_contract_is_minimal_and_json_safe():
    request = StockChoiceRequest.from_candidates("삼성", [candidate("005930", "삼성전자")])
    assert request.model_dump(mode="json") == {
        "query": "삼성",
        "candidates": [{"selected_code": "005930", "display_name": "삼성전자", "market": "KOSPI"}],
    }
    assert StockChoiceResume(selected_code="005930").selected_code == "005930"


def test_select_stock_handles_zero_one_many_and_membership():
    one = [candidate("005930", "삼성전자")]
    assert select_stock(one, None) == one[0]
    assert select_stock([], None) is None
    with pytest.raises(LookupError, match="selection required"):
        select_stock([*one, candidate("000660", "SK하이닉스")], None)
    with pytest.raises(ValueError, match="not offered"):
        select_stock(one, StockChoiceResume(selected_code="000660"))
    with pytest.raises(ValueError, match="duplicate"):
        select_stock([one[0], one[0]], StockChoiceResume(selected_code="005930"))
