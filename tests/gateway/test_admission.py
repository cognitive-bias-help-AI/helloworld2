import asyncio

import pytest

from app.gateway.admission import ProviderAdmissionController


@pytest.mark.asyncio
async def test_shared_controller_bounds_three_independent_clients():
    controller = ProviderAdmissionController({"dart": 2, "kiwoom": 1})
    release = asyncio.Event()
    active = 0
    max_active = 0
    two_started = asyncio.Event()

    async def client():
        nonlocal active, max_active
        async with controller.acquire("dart"):
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                two_started.set()
            await release.wait()
            active -= 1

    tasks = [asyncio.create_task(client()) for _ in range(3)]
    await asyncio.wait_for(two_started.wait(), timeout=1)
    assert max_active == 2
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_controllers_and_provider_pools_are_independent():
    first = ProviderAdmissionController({"dart": 1, "kiwoom": 1})
    second = ProviderAdmissionController({"dart": 1})
    async with (
        first.acquire("dart"),
        first.acquire("kiwoom"),
        second.acquire("dart"),
    ):
        pass


@pytest.mark.asyncio
async def test_unconfigured_provider_fails_closed():
    controller = ProviderAdmissionController({"dart": 1})
    with pytest.raises(ValueError, match="admission capacity"):
        async with controller.acquire("naver"):
            pass


@pytest.mark.asyncio
async def test_cancellation_releases_owned_permit():
    controller = ProviderAdmissionController({"dart": 1})
    entered = asyncio.Event()
    hold = asyncio.Event()

    async def owner():
        async with controller.acquire("dart"):
            entered.set()
            await hold.wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with asyncio.timeout(1):
        async with controller.acquire("dart"):
            pass
