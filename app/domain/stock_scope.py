"""Canonical stock 생성 전 exact instrument scope 계약."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.frozen import KRXCode, NonBlankStr


class _StockScopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class AssetType(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    PREFERRED_STOCK = "PREFERRED_STOCK"
    ETF = "ETF"
    ETN = "ETN"
    SPAC = "SPAC"
    OTHER = "OTHER"


class InstrumentCandidate(_StockScopeModel):
    """Exact resolver가 반환하는 pre-canonical instrument metadata."""

    code: KRXCode
    name: NonBlankStr
    market: Literal["KOSPI", "KOSDAQ"]
    asset_type: AssetType
    is_delisted: bool = False
    is_managed: bool = False


class ScopeRejection(StrEnum):
    UNSUPPORTED_ASSET_TYPE = "unsupported_asset_type"
    DELISTED = "delisted"


class ScopeDecision(_StockScopeModel):
    supported: bool
    rejection: ScopeRejection | None = None


def evaluate_stock_scope(candidate: InstrumentCandidate) -> ScopeDecision:
    """현재 제품이 지원하는 상장 주식인지 결정론적으로 판정한다."""

    if candidate.is_delisted:
        return ScopeDecision(supported=False, rejection=ScopeRejection.DELISTED)
    if candidate.asset_type not in {
        AssetType.COMMON_STOCK,
        AssetType.PREFERRED_STOCK,
    }:
        return ScopeDecision(
            supported=False,
            rejection=ScopeRejection.UNSUPPORTED_ASSET_TYPE,
        )
    return ScopeDecision(supported=True)
