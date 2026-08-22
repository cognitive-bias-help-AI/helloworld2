from pathlib import Path

from app.store.protocols import EvidenceStore

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db" / "migrations" / "0001_evidence_acquisition.sql"


def test_initial_acquisition_migration_is_the_single_schema_authority():
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "acquisition_queries",
        "provider_calls",
        "evidence",
        "evidence_query_links",
    ):
        assert f"CREATE TABLE {table}" in sql
    assert "UNIQUE (run_id, content_sha256)" in sql
    assert "PRIMARY KEY (evidence_id, query_id)" in sql
    assert "ON CONFLICT DO UPDATE" not in sql.upper()


def test_sql_store_implements_existing_protocol_by_explicit_pool_injection():
    from app.store.sql_evidence_store import SqlEvidenceStore

    pool = object()
    store = SqlEvidenceStore(pool)

    assert store.pool is pool
    assert isinstance(store, EvidenceStore)
