"""Minimal KRX Open API client for stock-master identity data."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


class KrxContractError(RuntimeError):
    """Raised when the KRX response does not match its documented contract."""


@dataclass(frozen=True)
class KrxRequest:
    url: str
    headers: dict[str, str]
    params: dict[str, str]


class KrxClient:
    _ENDPOINTS = {
        "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_isu_base_info",
        "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_isu_base_info",
    }

    def __init__(self, http_client, *, api_key: str) -> None:
        if not api_key.strip():
            raise ValueError("KRX_API_KEY must be non-blank")
        self._http_client = http_client
        self._api_key = api_key

    def request_for(
        self,
        market: Literal["KOSPI", "KOSDAQ"] | str,
        bas_dd: str,
    ) -> KrxRequest:
        if market not in self._ENDPOINTS:
            raise ValueError(f"unsupported KRX market: {market}")
        if re.fullmatch(r"[0-9]{8}", bas_dd) is None:
            raise ValueError("basDd must be YYYYMMDD")
        return KrxRequest(
            url=self._ENDPOINTS[market],
            headers={"AUTH_KEY": self._api_key, "accept": "application/json"},
            params={"basDd": bas_dd},
        )

    async def fetch_basic_info(
        self,
        market: Literal["KOSPI", "KOSDAQ"] | str,
        bas_dd: str,
    ) -> tuple[Mapping[str, object], ...]:
        request = self.request_for(market, bas_dd)
        response = await self._http_client.get(
            request.url,
            headers=request.headers,
            params=request.params,
        )
        response.raise_for_status()
        try:
            body = response.json()
        except (TypeError, ValueError) as exc:
            raise KrxContractError("KRX response is not valid JSON") from exc
        if not isinstance(body, dict):
            raise KrxContractError("KRX response must be an object")
        rows = body.get("OutBlock_1")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise KrxContractError("KRX OutBlock_1 must be a list of objects")
        return tuple(rows)
