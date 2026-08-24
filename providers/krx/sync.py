"""Validated, atomic KRX stock-master synchronization."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from app.domain.stock_master import (
    StockMasterSnapshot,
    parse_krx_rows,
    write_stock_master_atomic,
)


class KrxMasterSyncError(RuntimeError):
    """Raised when a complete common-date snapshot cannot be produced."""


SEOUL_TIMEZONE = timezone(timedelta(hours=9), name="Asia/Seoul")


async def _fetch_common_date(client, bas_dd: str):
    kospi = await client.fetch_basic_info("KOSPI", bas_dd)
    kosdaq = await client.fetch_basic_info("KOSDAQ", bas_dd)
    return kospi, kosdaq


async def sync_stock_master(
    client,
    target: str | Path,
    *,
    as_of: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> StockMasterSnapshot:
    if as_of is not None:
        dates = (as_of,)
    else:
        seoul_yesterday = clock().astimezone(SEOUL_TIMEZONE).date() - timedelta(days=1)
        dates = tuple(
            (seoul_yesterday - timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(7)
        )

    selected = None
    for bas_dd in dates:
        kospi_rows, kosdaq_rows = await _fetch_common_date(client, bas_dd)
        if kospi_rows and kosdaq_rows:
            selected = (bas_dd, kospi_rows, kosdaq_rows)
            break
        if as_of is not None:
            raise KrxMasterSyncError(f"KRX has no complete KOSPI/KOSDAQ data for {bas_dd}")
    if selected is None:
        raise KrxMasterSyncError("no common valid KRX market date found within 7 days")

    bas_dd, kospi_rows, kosdaq_rows = selected
    kospi, _ = parse_krx_rows(kospi_rows, expected_market="KOSPI")
    kosdaq, _ = parse_krx_rows(kosdaq_rows, expected_market="KOSDAQ")
    records = tuple(sorted((*kospi, *kosdaq), key=lambda item: item.code))
    snapshot = StockMasterSnapshot(
        schema_version="krx_stock_master/v1",
        source="KRX_OPEN_API",
        as_of=bas_dd,
        generated_at=clock().astimezone(UTC).isoformat(),
        record_count=len(records),
        records=records,
    )
    write_stock_master_atomic(target, snapshot)
    return snapshot
