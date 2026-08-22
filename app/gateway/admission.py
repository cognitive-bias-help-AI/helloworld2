"""Process-local provider admission with explicit lifecycle ownership."""

from __future__ import annotations

import asyncio


class ProviderAdmissionController:
    def __init__(self, capacities: dict[str, int]) -> None:
        if any(not isinstance(value, int) or value < 1 for value in capacities.values()):
            raise ValueError("provider admission capacity must be positive")
        self._pools = {
            provider: asyncio.Semaphore(capacity)
            for provider, capacity in capacities.items()
        }

    def acquire(self, provider: str) -> asyncio.Semaphore:
        pool = self._pools.get(provider)
        if pool is None:
            raise ValueError(f"provider admission capacity is not configured: {provider}")
        return pool
