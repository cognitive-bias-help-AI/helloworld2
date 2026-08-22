"""Thin NAVER API HUB news-search HTTP client.

Retries deliberately do not live here.  ``EvidenceGateway`` owns retry budget,
ProviderCall provenance, and concurrency admission for all providers.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

NAVER_NEWS_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"


class NaverNewsClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("NAVER client_id must be non-blank")
        if not isinstance(client_secret, str) or not client_secret.strip():
            raise ValueError("NAVER client_secret must be non-blank")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client

    async def search(
        self,
        *,
        query: str,
        display: int,
        sort: str,
        timeout_s: float,
    ) -> dict:
        headers = {
            "X-NCP-APIGW-API-KEY-ID": self._client_id,
            "X-NCP-APIGW-API-KEY": self._client_secret,
        }
        params = {"query": query, "display": display, "sort": sort}

        if self._client is None:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.get(
                    NAVER_NEWS_SEARCH_URL,
                    headers=headers,
                    params=params,
                    timeout=timeout_s,
                )
        else:
            response = await self._client.get(
                NAVER_NEWS_SEARCH_URL,
                headers=headers,
                params=params,
                timeout=timeout_s,
            )
        return self._envelope(response, query=query, display=display, sort=sort)

    @staticmethod
    def _envelope(
        response: httpx.Response,
        *,
        query: str,
        display: int,
        sort: str,
    ) -> dict:
        try:
            body = response.json()
        except (ValueError, TypeError):
            body = {"raw_text": response.text[:2000]}
        response_headers: Mapping[str, str] = response.headers
        return {
            "_meta": {
                "http_status": response.status_code,
                "headers": {str(k).lower(): str(v) for k, v in response_headers.items()},
                "request_query": query,
                "request_display": display,
                "request_sort": sort,
            },
            "body": body,
        }
