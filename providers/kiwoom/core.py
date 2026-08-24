"""Independent Kiwoom core for the three evidence-producing TRs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Protocol


class Environment(StrEnum):
    PRODUCTION = "production"
    MOCK = "mock"


class ResultStatus(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    ERROR = "error"


class ErrorCategory(StrEnum):
    INVALID_REQUEST = "invalid_request"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    IP_MISMATCH = "ip_mismatch"
    PROVIDER_CONTRACT = "provider_contract"
    NETWORK = "network"
    HTTP_SERVER = "http_server"
    HTTP_CLIENT = "http_client"
    PROVIDER = "provider"


@dataclass(frozen=True)
class KiwoomCredentials:
    app_key: str = field(repr=False)
    secret_key: str = field(repr=False)


@dataclass(frozen=True)
class KiwoomRequest:
    tr: str
    params: Mapping[str, str]
    environment: Environment
    continuation: bool = False
    next_key: str | None = None


@dataclass(frozen=True)
class RateLimitInfo:
    provider_message: str | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class ProviderError:
    category: ErrorCategory
    message: str
    retryable: bool
    code: int | str | None = None
    http_status: int | None = None
    limit_info: RateLimitInfo | None = None


@dataclass(frozen=True)
class AdapterResult:
    status: ResultStatus
    provider: str
    tr: str
    request_params: Mapping[str, str]
    data: Mapping[str, Any] | list[Mapping[str, Any]] | None = None
    has_more: bool = False
    next_key: str | None = None
    raw_reference: Mapping[str, Any] = field(default_factory=dict)
    error: ProviderError | None = None


class AsyncHttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Mapping[str, Any]: ...


class AsyncHttpClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, str],
    ) -> AsyncHttpResponse: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now().astimezone()


@dataclass(frozen=True)
class _TrContract:
    path: str
    required: frozenset[str]
    allowed: frozenset[str]


@dataclass(frozen=True)
class _TokenState:
    token: str = field(repr=False)
    expires_at: datetime


_TR_CONTRACTS = {
    "ka10007": _TrContract(
        "/api/dostk/mrkcond", frozenset({"stk_cd"}), frozenset({"stk_cd"})
    ),
    "ka10081": _TrContract(
        "/api/dostk/chart",
        frozenset({"stk_cd", "base_dt", "upd_stkpc_tp"}),
        frozenset({"stk_cd", "base_dt", "upd_stkpc_tp"}),
    ),
    "ka10059": _TrContract(
        "/api/dostk/stkinfo",
        frozenset({"dt", "stk_cd", "amt_qty_tp", "trde_tp", "unit_tp"}),
        frozenset({"dt", "stk_cd", "amt_qty_tp", "trde_tp", "unit_tp"}),
    ),
}
_BASE_URLS = {
    Environment.PRODUCTION: "https://api.kiwoom.com",
    Environment.MOCK: "https://mockapi.kiwoom.com",
}
_KST = timezone(timedelta(hours=9))
_RATE_LIMIT_CODES = frozenset({1700, 1701, 1702})
_RETRYABLE_AUTH_CODES = frozenset({8003, 8005, 8103})
_AUTH_CODES = frozenset(
    {8001, 8002, 8003, 8005, 8006, 8009, 8011, 8012, 8015, 8016, 8020, 8030, 8031, 8040, 8050, 8103}
)
_PROVIDER_CONTRACT_CODES = frozenset({1501, 1504, 1505, 1687}) | frozenset(
    range(1511, 1518)
)
_STOCK_CODE = re.compile(r"^[0-9]{6}(?:_(?:NX|AL))?$")


def supports_stock_code(value: object) -> bool:
    """Return whether the current Kiwoom upstream contract accepts the code."""

    return isinstance(value, str) and _STOCK_CODE.fullmatch(value) is not None


class KiwoomAdapter:
    """Injected HTTP client, authentication, one request, and normalization."""

    def __init__(
        self,
        http_client: AsyncHttpClient,
        credentials: KiwoomCredentials,
        *,
        clock: Clock | None = None,
        refresh_margin: timedelta = timedelta(minutes=1),
        transport_errors: tuple[type[BaseException], ...] = (
            TimeoutError,
            ConnectionError,
            OSError,
        ),
    ) -> None:
        self._http_client = http_client
        self._credentials = credentials
        self._clock = clock or SystemClock()
        self._refresh_margin = refresh_margin
        self._transport_errors = transport_errors
        self._tokens: dict[Environment, _TokenState] = {}

    async def authenticate(self, environment: Environment) -> AdapterResult:
        try:
            response = await self._http_client.post(
                _BASE_URLS[environment] + "/oauth2/token",
                headers={"Content-Type": "application/json;charset=UTF-8"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._credentials.app_key,
                    "secretkey": self._credentials.secret_key,
                },
            )
        except self._transport_errors:
            return self._error_result(
                KiwoomRequest("au10001", {}, environment),
                ProviderError(ErrorCategory.NETWORK, "token transport error", True),
            )
        body = dict(response.json())
        error = self._classify_response(response.status_code, response.headers, body)
        if error is not None:
            return self._error_result(KiwoomRequest("au10001", {}, environment), error)
        if not body.get("token") or not body.get("expires_dt"):
            return self._error_result(
                KiwoomRequest("au10001", {}, environment),
                ProviderError(
                    ErrorCategory.PROVIDER_CONTRACT,
                    "token response lacks required fields",
                    False,
                ),
            )
        expires_at = datetime.strptime(str(body["expires_dt"]), "%Y%m%d%H%M%S").replace(
            tzinfo=_KST
        )
        self._tokens[environment] = _TokenState(str(body["token"]), expires_at)
        return AdapterResult(ResultStatus.SUCCESS, "kiwoom", "au10001", {})

    async def request(self, request: KiwoomRequest) -> AdapterResult:
        validation = self._validate_request(request)
        if validation is not None:
            return self._error_result(request, validation)
        token = self._tokens.get(request.environment)
        if token is None or self._clock.now() + self._refresh_margin >= token.expires_at:
            auth = await self.authenticate(request.environment)
            if auth.status is ResultStatus.ERROR:
                return AdapterResult(
                    ResultStatus.ERROR,
                    "kiwoom",
                    request.tr,
                    dict(request.params),
                    error=auth.error,
                )
            token = self._tokens[request.environment]
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token.token}",
            "api-id": request.tr,
        }
        if request.continuation:
            headers.update({"cont-yn": "Y", "next-key": request.next_key or ""})
        try:
            response = await self._http_client.post(
                _BASE_URLS[request.environment] + _TR_CONTRACTS[request.tr].path,
                headers=headers,
                json=dict(request.params),
            )
        except self._transport_errors:
            return self._error_result(
                request, ProviderError(ErrorCategory.NETWORK, "transport error", True)
            )
        body = dict(response.json())
        error = self._classify_response(response.status_code, response.headers, body)
        if error is not None:
            return self._error_result(request, error)
        lowered = {str(key).lower(): str(value) for key, value in response.headers.items()}
        has_more = lowered.get("cont-yn", "").upper() == "Y"
        data = self._normalize_data(request, body)
        return AdapterResult(
            ResultStatus.EMPTY if data in ({}, []) else ResultStatus.SUCCESS,
            "kiwoom",
            request.tr,
            dict(request.params),
            data=data,
            has_more=has_more,
            next_key=lowered.get("next-key") if has_more else None,
            raw_reference={"api_id": lowered.get("api-id")},
        )

    @staticmethod
    def _validate_request(request: KiwoomRequest) -> ProviderError | None:
        contract = _TR_CONTRACTS.get(request.tr)
        if contract is None:
            return ProviderError(ErrorCategory.INVALID_REQUEST, "unsupported TR", False)
        supplied = set(request.params)
        if supplied != contract.required or supplied - contract.allowed:
            return ProviderError(ErrorCategory.INVALID_REQUEST, "invalid TR params", False)
        stock_code = request.params.get("stk_cd")
        if stock_code is not None and not supports_stock_code(stock_code):
            return ProviderError(ErrorCategory.INVALID_REQUEST, "invalid stock code", False)
        for field_name in {"dt", "base_dt"} & supplied:
            try:
                datetime.strptime(request.params[field_name], "%Y%m%d")
            except ValueError:
                return ProviderError(ErrorCategory.INVALID_REQUEST, "invalid date", False)
        allowed = {
            "upd_stkpc_tp": {"0", "1"},
            "amt_qty_tp": {"1", "2"},
            "trde_tp": {"0", "1", "2"},
            "unit_tp": {"1", "1000"},
        }
        if any(request.params.get(key) not in values for key, values in allowed.items() if key in supplied):
            return ProviderError(ErrorCategory.INVALID_REQUEST, "invalid enum", False)
        if request.continuation != bool(request.next_key):
            return ProviderError(ErrorCategory.INVALID_REQUEST, "invalid continuation", False)
        return None

    def _classify_response(
        self, status: int, headers: Mapping[str, Any], body: Mapping[str, Any]
    ) -> ProviderError | None:
        code = _normalized_code(body.get("return_code"))
        message = self._sanitize(body.get("return_msg"))
        retry_after = _retry_after(headers)
        if code in _RATE_LIMIT_CODES:
            return ProviderError(
                ErrorCategory.RATE_LIMIT,
                message,
                True,
                code,
                status,
                RateLimitInfo(message, retry_after),
            )
        if code == 8010:
            return ProviderError(ErrorCategory.IP_MISMATCH, message, False, code, status)
        if code in _AUTH_CODES:
            return ProviderError(
                ErrorCategory.AUTH,
                message,
                code in _RETRYABLE_AUTH_CODES,
                code,
                status,
            )
        if code in _PROVIDER_CONTRACT_CODES:
            return ProviderError(
                ErrorCategory.PROVIDER_CONTRACT, message, False, code, status
            )
        if status >= 500:
            return ProviderError(ErrorCategory.HTTP_SERVER, message, True, code, status)
        if status >= 400:
            return ProviderError(ErrorCategory.HTTP_CLIENT, message, False, code, status)
        if code not in (None, 0):
            return ProviderError(ErrorCategory.PROVIDER, message, False, code, status)
        return None

    def _sanitize(self, value: Any) -> str:
        message = "" if value in (None, "") else str(value)
        secrets = [self._credentials.app_key, self._credentials.secret_key]
        secrets.extend(token.token for token in self._tokens.values())
        for secret in secrets:
            if secret:
                message = message.replace(secret, "[REDACTED]")
        return message

    @staticmethod
    def _normalize_data(
        request: KiwoomRequest, body: Mapping[str, Any]
    ) -> Mapping[str, Any] | list[Mapping[str, Any]]:
        if request.tr == "ka10007":
            return _normalize_quote(body)
        if request.tr == "ka10081":
            return _normalize_daily(request, body)
        if request.tr == "ka10059":
            return _normalize_flow(request, body)
        return {}

    @staticmethod
    def _error_result(request: KiwoomRequest, error: ProviderError) -> AdapterResult:
        return AdapterResult(
            ResultStatus.ERROR,
            "kiwoom",
            request.tr,
            dict(request.params),
            error=error,
        )


def _normalized_code(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return int(text) if text.lstrip("-").isdigit() else text


def _retry_after(headers: Mapping[str, Any]) -> int | None:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    value = lowered.get("retry-after")
    return int(value) if value is not None and value.isdigit() else None


def _copy_int(target: dict[str, Any], name: str, source: Mapping[str, Any], key: str, *, absolute: bool = False) -> None:
    value = source.get(key)
    if value in (None, ""):
        return
    number = int(str(value).replace(",", ""))
    target[name] = abs(number) if absolute else number


def _normalize_quote(body: Mapping[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if body.get("stk_cd") is not None:
        data["stock_code"] = body["stk_cd"]
    for name, key in (("current_price", "cur_prc"), ("open", "open_pric"), ("high", "high_pric"), ("low", "low_pric"), ("volume", "trde_qty")):
        _copy_int(data, name, body, key, absolute=True)
    _copy_int(data, "turnover", body, "trde_prica", absolute=True)
    if "turnover" in data:
        data["turnover_unit"] = "million_krw"
    if body.get("date") not in (None, ""):
        data["as_of_date"] = datetime.strptime(str(body["date"]), "%Y%m%d").date().isoformat()
    if body.get("tm") not in (None, ""):
        data["as_of_time"] = datetime.strptime(str(body["tm"]), "%H%M%S").time().isoformat()
    return data


def _normalize_daily(request: KiwoomRequest, body: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for raw in body.get("stk_dt_pole_chart_qry") or []:
        row = dict(raw)
        item: dict[str, Any] = {
            "stock_code": body.get("stk_cd") or request.params["stk_cd"],
            "adjusted_price": request.params["upd_stkpc_tp"] == "1",
        }
        if row.get("dt") not in (None, ""):
            item["date"] = datetime.strptime(str(row["dt"]), "%Y%m%d").date().isoformat()
        for name, key in (("open", "open_pric"), ("high", "high_pric"), ("low", "low_pric"), ("close", "cur_prc"), ("volume", "trde_qty")):
            _copy_int(item, name, row, key, absolute=True)
        _copy_int(item, "turnover", row, "trde_prica", absolute=True)
        if "turnover" in item:
            item["turnover_unit"] = "million_krw"
        result.append(item)
    return result


def _normalize_flow(request: KiwoomRequest, body: Mapping[str, Any]) -> list[dict[str, Any]]:
    measure = "amount" if request.params["amt_qty_tp"] == "1" else "quantity"
    trade_kind = {"0": "net_buy", "1": "buy", "2": "sell"}[request.params["trde_tp"]]
    unit = "million_krw" if measure == "amount" else ("thousand_shares" if request.params["unit_tp"] == "1000" else "shares")
    result = []
    for raw in body.get("stk_invsr_orgn") or []:
        row = dict(raw)
        item: dict[str, Any] = {
            "stock_code": request.params["stk_cd"],
            "measure": measure,
            "trade_kind": trade_kind,
            "unit": unit,
            "source_unit": request.params["unit_tp"],
        }
        if row.get("dt") not in (None, ""):
            item["date"] = datetime.strptime(str(row["dt"]), "%Y%m%d").date().isoformat()
        for name, key in (("individual", "ind_invsr"), ("foreigner", "frgnr_invsr"), ("institution", "orgn")):
            _copy_int(item, name, row, key)
        result.append(item)
    return result
