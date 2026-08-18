import pytest
from pydantic import ValidationError

from app.domain.stock_scope import (
    AssetType,
    InstrumentCandidate,
    ScopeRejection,
    evaluate_stock_scope,
)


def instrument(**changes) -> InstrumentCandidate:
    values = {
        "code": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "asset_type": AssetType.COMMON_STOCK,
        "is_delisted": False,
        "is_managed": False,
    }
    return InstrumentCandidate(**(values | changes))


@pytest.mark.parametrize("asset_type", [AssetType.COMMON_STOCK, AssetType.PREFERRED_STOCK])
def test_scope_allows_supported_stock_types_and_preserves_managed_flag(asset_type):
    candidate = instrument(asset_type=asset_type, is_managed=True)

    decision = evaluate_stock_scope(candidate)

    assert decision.supported is True
    assert decision.rejection is None
    assert candidate.is_managed is True


@pytest.mark.parametrize(
    "asset_type",
    [AssetType.ETF, AssetType.ETN, AssetType.SPAC, AssetType.OTHER],
)
def test_scope_rejects_unsupported_asset_types(asset_type):
    decision = evaluate_stock_scope(instrument(asset_type=asset_type))

    assert decision.supported is False
    assert decision.rejection is ScopeRejection.UNSUPPORTED_ASSET_TYPE


def test_scope_rejects_delisted_supported_stock():
    decision = evaluate_stock_scope(instrument(is_delisted=True))

    assert decision.supported is False
    assert decision.rejection is ScopeRejection.DELISTED


def test_instrument_contract_is_frozen_strict_and_accepts_preferred_code():
    candidate = instrument(code="03473K", asset_type=AssetType.PREFERRED_STOCK)

    assert candidate.code == "03473K"
    with pytest.raises(ValidationError):
        candidate.name = "변경"
    with pytest.raises(ValidationError, match="Extra inputs"):
        InstrumentCandidate(**candidate.model_dump(), score=1.0)
