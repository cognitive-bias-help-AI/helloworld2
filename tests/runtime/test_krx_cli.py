from __future__ import annotations

from pathlib import Path

import pytest

from app import cli
from tests.domain.test_stock_master import snapshot


@pytest.mark.asyncio
async def test_KRX_sync는_canonical_KRX_API_KEY만_읽고_target과_as_of를_전달한다(
    tmp_path, monkeypatch
):
    seen = {}

    class HttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class KrxClient:
        def __init__(self, http_client, *, api_key):
            seen["api_key"] = api_key

    async def fake_sync(client, target, *, as_of):
        seen["target"] = target
        seen["as_of"] = as_of
        return snapshot()

    monkeypatch.setenv("KRX_API_KEY", "canonical-key")
    monkeypatch.setattr("httpx.AsyncClient", HttpClient)
    monkeypatch.setattr("providers.krx.client.KrxClient", KrxClient)
    monkeypatch.setattr("providers.krx.sync.sync_stock_master", fake_sync)
    target = tmp_path / "master.json"

    result = await cli._krx_master_sync(target, "20260821")

    assert result == 0
    assert seen == {
        "api_key": "canonical-key",
        "target": target,
        "as_of": "20260821",
    }


@pytest.mark.asyncio
async def test_KRX_sync는_key_누락시_HTTP_client를_생성하지_않는다(tmp_path, monkeypatch):
    monkeypatch.delenv("KRX_API_KEY", raising=False)

    class ForbiddenClient:
        def __init__(self):
            raise AssertionError("HTTP must not be created")

    monkeypatch.setattr("httpx.AsyncClient", ForbiddenClient)

    assert await cli._krx_master_sync(tmp_path / "master.json", None) == 1


def test_cli_parser는_KRX_sync와_기본_snapshot_path를_노출한다(monkeypatch):
    captured = {}

    async def fake_sync(target: Path, as_of: str | None):
        captured.update(target=target, as_of=as_of)
        return 0

    monkeypatch.setattr(cli, "load_dotenv", lambda path: 0)
    monkeypatch.setattr(cli, "_krx_master_sync", fake_sync)

    assert cli.main(["krx-master-sync", "--as-of", "20260821"]) == 0
    assert captured == {
        "target": Path("data/krx_stock_master.json"),
        "as_of": "20260821",
    }
