"""Minimal asynchronous OpenDART HTTP client."""

from __future__ import annotations

import httpx

FINANCIAL_STATEMENT_URL = (
    "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
)
DISCLOSURE_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
FINANCIAL_INDICATOR_URL = "https://opendart.fss.or.kr/api/fnlttSinglIndx.json"


class OpenDartClient:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        if not api_key.strip():
            raise ValueError("OpenDART api_key must be non-blank")
        self._api_key = api_key
        self._client = client

    async def financial_statement(
        self,
        *,
        corp_code: str,
        business_year: str,
        report_code: str,
        fs_div: str,
        timeout_s: float = 10.0,
    ) -> dict:
        params = {
            "corp_code": corp_code,
            "bsns_year": business_year,
            "reprt_code": report_code,
            "fs_div": fs_div,
            "crtfc_key": self._api_key,
        }
        if self._client is not None:
            response = await self._client.get(
                FINANCIAL_STATEMENT_URL, params=params, timeout=timeout_s
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    FINANCIAL_STATEMENT_URL, params=params, timeout=timeout_s
                )
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict):
            raise ValueError("OpenDART response must be a JSON object")
        return raw

    async def disclosure_list(
        self,
        *,
        corp_code: str,
        bgn_de: str | None = None,
        end_de: str | None = None,
        last_reprt_at: str | None = None,
        pblntf_ty: str | None = None,
        pblntf_detail_ty: str | None = None,
        sort: str | None = None,
        sort_mth: str | None = None,
        page_no: int = 1,
        page_count: int = 20,
        timeout_s: float = 10.0,
    ) -> dict:
        params = {
            key: value
            for key, value in {
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "last_reprt_at": last_reprt_at,
                "pblntf_ty": pblntf_ty,
                "pblntf_detail_ty": pblntf_detail_ty,
                "sort": sort,
                "sort_mth": sort_mth,
                "page_no": page_no,
                "page_count": page_count,
                "crtfc_key": self._api_key,
            }.items()
            if value is not None
        }
        if self._client is not None:
            response = await self._client.get(
                DISCLOSURE_LIST_URL, params=params, timeout=timeout_s
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    DISCLOSURE_LIST_URL, params=params, timeout=timeout_s
                )
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict):
            raise ValueError("OpenDART response must be a JSON object")
        return raw

    async def financial_indicator(
        self,
        *,
        corp_code: str,
        business_year: str,
        report_code: str,
        indicator_class_code: str,
        timeout_s: float = 10.0,
    ) -> dict:
        params = {
            "corp_code": corp_code,
            "bsns_year": business_year,
            "reprt_code": report_code,
            "idx_cl_code": indicator_class_code,
            "crtfc_key": self._api_key,
        }
        if self._client is not None:
            response = await self._client.get(
                FINANCIAL_INDICATOR_URL, params=params, timeout=timeout_s
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    FINANCIAL_INDICATOR_URL, params=params, timeout=timeout_s
                )
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict):
            raise ValueError("OpenDART response must be a JSON object")
        return raw
