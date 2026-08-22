import os
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.store.sql_evidence_store import SqlEvidenceStore

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "migrations" / "0001_evidence_acquisition.sql"


def _test_dsn() -> str:
    test_dsn = os.getenv("TEST_POSTGRES_DSN")
    if not test_dsn:
        pytest.skip("TEST_POSTGRES_DSN is not configured; PostgreSQL tests not executed")
    normal_dsn = os.getenv("POSTGRES_DSN")
    if normal_dsn and test_dsn == normal_dsn:
        pytest.fail("TEST_POSTGRES_DSN must differ from POSTGRES_DSN")
    return test_dsn


@pytest_asyncio.fixture
async def postgres_pool():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(
            """
            DROP TABLE IF EXISTS evidence_query_links;
            DROP TABLE IF EXISTS evidence;
            DROP TABLE IF EXISTS provider_calls;
            DROP TABLE IF EXISTS acquisition_queries;
            """
        )
        await connection.execute(MIGRATION.read_text(encoding="utf-8"))
    finally:
        await connection.close()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def sql_store(postgres_pool):
    return SqlEvidenceStore(postgres_pool)
