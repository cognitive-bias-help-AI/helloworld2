from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.stock_master import load_stock_master, write_stock_master_atomic
from providers.krx.sync import KrxMasterSyncError, sync_stock_master
from tests.domain.test_stock_master import row, snapshot

NOW = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)


class Client:
    def __init__(self, outcomes):
        self.outcomes = dict(outcomes)
        self.calls = []

    async def fetch_basic_info(self, market, bas_dd):
        self.calls.append((market, bas_dd))
        outcome = self.outcomes[(market, bas_dd)]
        if isinstance(outcome, BaseException):
            raise outcome
        return tuple(outcome)


@pytest.mark.asyncio
async def test_explicit_as_of는_두_market_동일일자_complete_validation후_replace한다(tmp_path):
    target = tmp_path / "master.json"
    client = Client(
        {
            ("KOSPI", "20260821"): [row("005930", "삼성전자", "KOSPI")],
            ("KOSDAQ", "20260821"): [row("247540", "에코프로비엠", "KOSDAQ")],
        }
    )

    result = await sync_stock_master(
        client,
        target,
        as_of="20260821",
        clock=lambda: NOW,
    )

    loaded = load_stock_master(target)
    assert result == loaded
    assert loaded.as_of == "20260821"
    assert [item.code for item in loaded.records] == ["005930", "247540"]
    assert client.calls == [("KOSPI", "20260821"), ("KOSDAQ", "20260821")]


@pytest.mark.asyncio
async def test_failed_new_sync는_existing_valid_snapshot을_보존한다(tmp_path):
    target = tmp_path / "master.json"
    write_stock_master_atomic(target, snapshot())
    before = target.read_bytes()
    client = Client(
        {
            ("KOSPI", "20260821"): [row("005930", "삼성전자", "KOSPI")],
            ("KOSDAQ", "20260821"): RuntimeError("KRX unavailable"),
        }
    )

    with pytest.raises(RuntimeError, match="unavailable"):
        await sync_stock_master(client, target, as_of="20260821", clock=lambda: NOW)

    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_invalid_second_market_batch도_existing_snapshot을_보존한다(tmp_path):
    target = tmp_path / "master.json"
    write_stock_master_atomic(target, snapshot())
    before = target.read_bytes()
    client = Client(
        {
            ("KOSPI", "20260821"): [row("005930", "삼성전자", "KOSPI")],
            ("KOSDAQ", "20260821"): [row("005930", "중복", "KOSDAQ")],
        }
    )

    with pytest.raises(ValueError, match="duplicate"):
        await sync_stock_master(client, target, as_of="20260821", clock=lambda: NOW)

    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_default_date_search는_서울_어제부터_최대_7일의_공통유효일만_선택한다(tmp_path):
    dates = ["20260823", "20260822", "20260821"]
    outcomes = {}
    for date in dates[:2]:
        outcomes[("KOSPI", date)] = []
        outcomes[("KOSDAQ", date)] = []
    outcomes[("KOSPI", "20260821")] = [row("005930", "삼성전자", "KOSPI")]
    outcomes[("KOSDAQ", "20260821")] = [row("247540", "에코프로비엠", "KOSDAQ")]
    client = Client(outcomes)

    result = await sync_stock_master(client, tmp_path / "master.json", clock=lambda: NOW)

    assert result.as_of == "20260821"


@pytest.mark.asyncio
async def test_7일_내_common_valid_date가_없으면_fail_closed한다(tmp_path):
    outcomes = {}
    for day in range(23, 16, -1):
        date = f"202608{day:02d}"
        outcomes[("KOSPI", date)] = []
        outcomes[("KOSDAQ", date)] = []
    client = Client(outcomes)

    with pytest.raises(KrxMasterSyncError, match="7"):
        await sync_stock_master(client, tmp_path / "master.json", clock=lambda: NOW)

    assert len(client.calls) == 14
    assert not (tmp_path / "master.json").exists()
