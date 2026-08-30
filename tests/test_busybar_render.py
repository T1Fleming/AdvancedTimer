"""Tests for BusyBarClient's clear-before-draw strategy (app/busybar_client.py).

Regression coverage for a visible bug: _render() used to clear the display before
every draw, which left a gap with none of our elements on screen. The device's own
mode screen (apps, settings) showed through that gap, flashing on every redraw -
very obvious while spinning the scroll wheel, which redraws rapidly.
"""
import asyncio

import pytest

from app.busybar_client import BusyBarClient


@pytest.fixture
def client(monkeypatch):
    c = BusyBarClient()
    calls = []

    async def fake_draw(elements):
        calls.append(("draw", tuple(e["id"] for e in elements)))
        return True

    async def fake_clear():
        calls.append(("clear", None))
        c._drawn = None

    monkeypatch.setattr(c, "draw", fake_draw)
    monkeypatch.setattr(c, "clear_display", fake_clear)
    c.calls = calls
    return c


HOME = [{"id": "spine", "type": "rectangle"}, {"id": "row2", "type": "text"}]
RUNNING = [{"id": "spine", "type": "rectangle"}, {"id": "row2_count", "type": "countdown"}]


def kinds(client):
    return [kind for kind, _ in client.calls]


def test_first_render_clears_because_display_state_is_unknown(client):
    asyncio.run(client._render(HOME))
    assert kinds(client) == ["clear", "draw"]


def test_repeated_render_of_the_same_elements_does_not_clear(client):
    """Spinning the wheel on the home screen redraws the same element set with new
    text/colors - re-posting an id updates it in place, so no clear is needed."""
    asyncio.run(client._render(HOME))
    client.calls.clear()

    for _ in range(5):
        asyncio.run(client._render(HOME))

    assert kinds(client) == ["draw"] * 5  # no clear, so nothing flashes through


def test_changing_the_element_set_clears_first(client):
    """A text->countdown swap needs the clear: the API rejects reusing an id with a
    different type."""
    asyncio.run(client._render(HOME))
    client.calls.clear()

    asyncio.run(client._render(RUNNING))

    assert kinds(client) == ["clear", "draw"]


def test_failed_draw_forces_a_clear_on_the_next_render(client):
    """A draw that didn't land leaves the display in an unknown state."""
    asyncio.run(client._render(HOME))
    client.calls.clear()

    succeed = False

    async def flaky_draw(elements):
        client.calls.append(("draw", None))
        return succeed

    client.draw = flaky_draw

    asyncio.run(client._render(HOME))
    assert kinds(client) == ["draw"]  # same element set, so still no clear

    succeed = True
    client.calls.clear()
    asyncio.run(client._render(HOME))
    assert kinds(client) == ["clear", "draw"]  # the failure invalidated our cache


def test_invalidate_forces_a_clear(client):
    """Used on device reconnect, where the bar may have rebooted and lost our draw."""
    asyncio.run(client._render(HOME))
    client.calls.clear()

    client.invalidate()
    asyncio.run(client._render(HOME))

    assert kinds(client) == ["clear", "draw"]
