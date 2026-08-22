"""Reuse the authoritative PostgreSQL migration fixture for runtime integration."""

from tests.store.conftest import postgres_pool as postgres_pool

__all__ = ("postgres_pool",)
