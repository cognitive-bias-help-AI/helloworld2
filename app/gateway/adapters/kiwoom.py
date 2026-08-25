"""Main ProviderAdapter bridge for the independent Kiwoom core."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol

import httpx

from app.gateway.execution import ProviderExecutionError
from app.schemas.frozen import EvidenceDraft, Query, RateLimitHint, ReasonCode, Request
from providers.kiwoom.core import (
    AdapterResult,
    Environment,
    ErrorCategory,
    KiwoomRequest,
    ResultStatus,
)

_SEMANTIC_TR = {
    "current_quote": "ka10007",
    "daily_price_history": "ka10081",
    "investor_flow": "ka10059",
}
_ERROR_REASONS = {
    ErrorCategory.INVALID_REQUEST: ReasonCode.SCHEMA_INVALID,
    ErrorCategory.INPUT_VALIDATION: ReasonCode.SCHEMA_INVALID,
    ErrorCategory.RATE_LIMIT: ReasonCode.RATE_LIMIT,
    ErrorCategory.SYMBOL_NOT_FOUND: ReasonCode.NO_RESULT,
    ErrorCategory.INVALID_CREDENTIAL: ReasonCode.AUTH_FAILED,
    ErrorCategory.INVALID_TOKEN: ReasonCode.AUTH_FAILED,
    ErrorCategory.MODE_MISMATCH: ReasonCode.AUTH_FAILED,
    ErrorCategory.DEVICE_AUTH: ReasonCode.AUTH_FAILED,
    ErrorCategory.AUTH: ReasonCode.AUTH_FAILED,
    ErrorCategory.IP_MISMATCH: ReasonCode.IP_MISMATCH,
    ErrorCategory.PROVIDER_CONTRACT: ReasonCode.CONTRACT_VIOLATION,
    ErrorCategory.NETWORK: ReasonCode.UPSTREAM_TIMEOUT,
    ErrorCategory.HTTP_SERVER: ReasonCode.UPSTREAM_5XX,
    ErrorCategory.HTTP_CLIENT: ReasonCode.NO_RESULT,
    ErrorCategory.PROVIDER: ReasonCode.NO_RESULT,
}


class KiwoomCore(Protocol):
    async def request(self, request: KiwoomRequest) -> AdapterResult: ...


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _raw_span(kind: str, item: dict) -> str:
    return json.dumps(
        {"kind": kind, **item},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _draft(kind: str, source_ref: str, item: dict) -> EvidenceDraft:
    return EvidenceDraft(
        source_type="quote",
        source_ref=source_ref,
        source_url=None,
        publisher="키움증권",
        published_at=None,
        raw_span=_raw_span(kind, item),
        span_scope="structured_field",
        normalized_value={"kind": kind, **item},
    )


class KiwoomAdapter:
    name: Literal["kiwoom"] = "kiwoom"
    max_concurrency = 1

    def __init__(self, core: KiwoomCore, *, environment: Environment) -> None:
        self._core = core
        self._environment = environment

    def build_request(self, q: Query, as_of: datetime) -> Request:
        del as_of
        if q.provider != self.name:
            raise ValueError("Query provider does not match KiwoomAdapter")
        tr = _SEMANTIC_TR.get(q.endpoint)
        if tr is None:
            raise ValueError(f"unsupported Kiwoom endpoint: {q.endpoint}")
        params = {
            "current_quote": self._quote_params,
            "daily_price_history": self._daily_params,
            "investor_flow": self._flow_params,
        }[q.endpoint](q.params)
        return Request(
            provider="kiwoom",
            endpoint=tr,
            method="POST",
            params=params,
            timeout_s=10.0,
        )

    @staticmethod
    def _stock_code(value: object) -> str:
        if not isinstance(value, str) or not value.isdigit() or len(value) != 6:
            raise ValueError("stock_code must be six digits")
        return value

    @classmethod
    def _quote_params(cls, params: dict) -> dict[str, str]:
        if set(params) != {"stock_code"}:
            raise ValueError("current_quote params do not match v1 contract")
        return {"stk_cd": cls._stock_code(params["stock_code"])}

    @classmethod
    def _daily_params(cls, params: dict) -> dict[str, str]:
        if set(params) != {"stock_code", "base_date", "adjusted_price"}:
            raise ValueError("daily_price_history params do not match v1 contract")
        base_date = params["base_date"]
        adjusted = params["adjusted_price"]
        if not isinstance(base_date, str):
            raise ValueError("base_date must be YYYYMMDD")
        try:
            datetime.strptime(base_date, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("base_date must be a valid YYYYMMDD") from exc
        if not isinstance(adjusted, bool):
            raise ValueError("adjusted_price must be bool")
        return {
            "stk_cd": cls._stock_code(params["stock_code"]),
            "base_dt": base_date,
            "upd_stkpc_tp": "1" if adjusted else "0",
        }

    @classmethod
    def _flow_params(cls, params: dict) -> dict[str, str]:
        required = {"stock_code", "date", "measure", "trade_kind", "unit"}
        if set(params) != required:
            raise ValueError("investor_flow params do not match v1 contract")
        date = params["date"]
        if not isinstance(date, str):
            raise ValueError("date must be YYYYMMDD")
        try:
            datetime.strptime(date, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("date must be a valid YYYYMMDD") from exc
        measure_codes = {"amount": "1", "quantity": "2"}
        trade_codes = {"net_buy": "0", "buy": "1", "sell": "2"}
        unit_codes = {
            ("amount", "million_krw"): "1",
            ("quantity", "shares"): "1",
            ("quantity", "thousand_shares"): "1000",
        }
        measure = params["measure"]
        trade_kind = params["trade_kind"]
        unit = params["unit"]
        if measure not in measure_codes or trade_kind not in trade_codes:
            raise ValueError("unsupported investor_flow semantic value")
        unit_code = unit_codes.get((measure, unit))
        if unit_code is None:
            raise ValueError("unit is incompatible with measure")
        return {
            "dt": date,
            "stk_cd": cls._stock_code(params["stock_code"]),
            "amt_qty_tp": measure_codes[measure],
            "trde_tp": trade_codes[trade_kind],
            "unit_tp": unit_code,
        }

    async def acall(self, req: Request) -> dict:
        if (
            req.provider != self.name
            or req.endpoint not in set(_SEMANTIC_TR.values())
            or req.method != "POST"
            or any(not isinstance(value, str) for value in req.params.values())
        ):
            raise ValueError("Request does not belong to KiwoomAdapter")
        try:
            result = await self._core.request(
                KiwoomRequest(
                    tr=req.endpoint,
                    params=req.params,
                    environment=self._environment,
                )
            )
        except httpx.TimeoutException as exc:
            raise ProviderExecutionError(
                reason_code=ReasonCode.UPSTREAM_TIMEOUT,
                retryable=True,
                safe_detail="Kiwoom request timed out",
            ) from exc
        return self.encode_result(result)

    @staticmethod
    def encode_result(result: AdapterResult) -> dict:
        return _json_value(asdict(result))

    def parse_response(self, raw: dict, q: Query) -> list[EvidenceDraft]:
        request = self.build_request(q, q.created_at)
        if raw.get("provider") != self.name or raw.get("tr") != request.endpoint:
            raise ValueError("Kiwoom result lineage does not match Query")
        status = raw.get("status")
        if status == ResultStatus.EMPTY.value:
            return []
        if status != ResultStatus.SUCCESS.value or raw.get("error") is not None:
            raise ValueError("Kiwoom error result cannot be parsed as EvidenceDraft")
        data = raw.get("data")
        if q.endpoint == "current_quote":
            if not isinstance(data, dict) or not data:
                raise ValueError("current_quote requires an object")
            stock_code = data.get("stock_code")
            if stock_code != q.params["stock_code"]:
                raise ValueError("current_quote stock_code mismatch")
            return [_draft("current_quote", f"ka10007:{stock_code}", data)]
        if not isinstance(data, list):
            raise ValueError("Kiwoom history result requires a list")
        drafts = []
        for item in data:
            if not isinstance(item, dict) or item.get("stock_code") != q.params["stock_code"]:
                raise ValueError("Kiwoom row stock_code mismatch")
            if q.endpoint == "daily_price_history":
                date = item.get("date")
                if not isinstance(date, str):
                    raise ValueError("daily price row requires date")
                adjustment = "adjusted" if item.get("adjusted_price") else "raw"
                drafts.append(
                    _draft(
                        "daily_price",
                        f"ka10081:{item['stock_code']}:{date}:{adjustment}",
                        item,
                    )
                )
            else:
                required = ("date", "measure", "trade_kind", "unit")
                if any(not isinstance(item.get(key), str) for key in required):
                    raise ValueError("investor flow row lacks identity")
                drafts.append(
                    _draft(
                        "investor_flow",
                        "ka10059:"
                        f"{item['stock_code']}:{item['date']}:{item['measure']}:"
                        f"{item['trade_kind']}:{item['unit']}",
                        item,
                    )
                )
        return drafts

    def classify_error(self, raw: dict) -> tuple[ReasonCode, bool]:
        error = raw.get("error")
        if raw.get("status") != ResultStatus.ERROR.value or not isinstance(error, dict):
            raise ValueError("Kiwoom raw result has no classifiable error")
        try:
            category = ErrorCategory(error["category"])
        except (KeyError, ValueError) as exc:
            raise ValueError("unknown Kiwoom error category") from exc
        retryable = error.get("retryable")
        if not isinstance(retryable, bool):
            raise ValueError("Kiwoom error retryable must be bool")
        if category is ErrorCategory.RATE_LIMIT and error.get("code") != 1700:
            return ReasonCode.CONTRACT_VIOLATION, False
        return _ERROR_REASONS[category], retryable

    def rate_limit_hint(self, raw: dict) -> RateLimitHint | None:
        error = raw.get("error")
        if (
            not isinstance(error, dict)
            or error.get("category") != ErrorCategory.RATE_LIMIT.value
            or error.get("code") != 1700
        ):
            return None
        info = error.get("limit_info")
        if not isinstance(info, dict):
            return RateLimitHint(provider="kiwoom", source="status_only")
        seconds = info.get("retry_after_seconds")
        if seconds is not None and (not isinstance(seconds, int) or seconds < 0):
            raise ValueError("invalid Kiwoom retry_after_seconds")
        return RateLimitHint(
            provider="kiwoom",
            retry_after_ms=None if seconds is None else seconds * 1000,
            source="header" if seconds is not None else "body_message",
        )
