"""Tests for device_listener()'s self-healing supervisor loop (app/server.py).

Regression coverage for a real bug hit during development: the first version of
this supervisor only backed off (via asyncio.sleep) on the exception path, so a
clean-but-unexpected return from _device_listener() caused a tight, CPU-pegging
busy-loop instead of a graceful restart.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app import server


async def _run_supervisor_until(done_event, timeout=1):
    task = asyncio.create_task(server.device_listener())
    try:
        await asyncio.wait_for(done_event.wait(), timeout=timeout)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_recovers_from_unexpected_exception(monkeypatch):
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock())
    calls = []

    async def scenario():
        done = asyncio.Event()

        async def flaky():
            calls.append("call")
            if len(calls) == 1:
                raise RuntimeError("boom")
            done.set()
            await asyncio.Event().wait()  # hang until cancelled, like the real listener would

        monkeypatch.setattr(server, "_device_listener", flaky)
        await _run_supervisor_until(done)

    asyncio.run(scenario())

    assert len(calls) == 2  # first call raised, supervisor restarted it


def test_backs_off_on_clean_return_not_just_exceptions(monkeypatch):
    """A clean (non-exception) return from _device_listener() must still go through
    the backoff sleep, not restart in a tight busy-loop."""
    sleep_mock = AsyncMock()
    monkeypatch.setattr(server.asyncio, "sleep", sleep_mock)
    calls = []

    async def scenario():
        done = asyncio.Event()

        async def clean_then_hang():
            calls.append("call")
            if len(calls) == 1:
                return  # clean return, no exception
            done.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_device_listener", clean_then_hang)
        await _run_supervisor_until(done)

    asyncio.run(scenario())

    assert len(calls) == 2
    sleep_mock.assert_awaited()
