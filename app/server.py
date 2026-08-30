#!/usr/bin/env python3
"""BUSY Bar time tracker web server.

Runs the BUSY Bar device listener (WebSocket button + scroll-wheel events, display
draws) and a local web UI in one asyncio event loop, so the physical bar and any
browser on the network are two equivalent, always-in-sync ways to drive the same
session - including picking its label, which the wheel and the web dropdown both
feed into one shared selection.

Requires the device to be in "apps" mode (POST /api/input?key=apps is called
automatically on every WebSocket connect/reconnect, not just at startup) - this
is the only mode that both streams button events AND allows our display draws
through, since it doesn't run a native BUSY/CUSTOM focus-timer session competing
for display priority.

Run as a single process: no --reload, no multiple workers - the Tracker, the
device WebSocket connection, and the SSE broadcaster are in-process singletons.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config, labels_store, sessions_store, state_store
from .broadcaster import StateBroadcaster
from .busybar_client import ACTION_PRESS, BTN_OK, BTN_START, EV_BUTTON, EV_ENCODER, BusyBarClient
from .tracker_core import IDLE, PAUSED, PENDING_LABEL, RUNNING

tracker = state_store.load_or_new_tracker()
busybar = BusyBarClient()
broadcaster = StateBroadcaster()

_redraw_task = None  # in-flight debounced redraw, see schedule_redraw()


class LabelBody(BaseModel):
    label: str = ""
    description: str = ""


class DescriptionBody(BaseModel):
    description: str = ""


class NewLabelBody(BaseModel):
    name: str
    color: str = ""


def full_snapshot():
    """The tracker snapshot plus the label set, which the browser needs to render.

    Tracker.snapshot() stays I/O-free, so the labels are attached here instead.
    Shipping them inside every snapshot means SSE already fans label edits out to
    every open browser - no second event channel needed.
    """
    snapshot = tracker.snapshot()
    snapshot["labels"] = labels_store.read_labels()
    return snapshot


def _today_seconds():
    """Active seconds today, including the in-progress session's time so far."""
    total = sessions_store.total_seconds_today()
    if tracker.app_state in (RUNNING, PAUSED):
        total += tracker.accumulated
    return total


async def redraw_and_broadcast():
    """Redraw the bar's display for the current tracker state and notify SSE subscribers.

    Reused after every mutation, and again on every device (re)connect so the bar's
    display can't drift from server-side state after it loses power independently.
    """
    label = tracker.selected_label
    color = labels_store.color_for(label)
    today = _today_seconds()
    if tracker.app_state == RUNNING:
        await busybar.draw_running(tracker.virtual_start_ts(), label, color, today)
    elif tracker.app_state == PAUSED:
        await busybar.draw_paused(tracker.accumulated, label, color, today)
    elif tracker.app_state == PENDING_LABEL:
        await busybar.draw_pending_label(tracker.pending["total_active_seconds"], label, color, today)
    elif tracker.app_state == IDLE:
        await busybar.draw_home(label, color, today)
    broadcaster.publish(full_snapshot())


async def apply_and_broadcast(mutation):
    mutation(tracker)
    state_store.save_state(tracker)
    await redraw_and_broadcast()


def apply_and_publish(mutation):
    """Mutate, persist, and notify browsers - without redrawing the bar.

    For state the bar doesn't render. The description is typed, so this fires
    repeatedly; routing it through apply_and_broadcast() would put an HTTP draw on
    the device behind every keystroke batch for something it never shows.
    """
    mutation(tracker)
    state_store.save_state(tracker)
    broadcaster.publish(full_snapshot())


async def _delayed_redraw(delay):
    try:
        await asyncio.sleep(delay)
        await redraw_and_broadcast()
    except asyncio.CancelledError:
        pass  # superseded by a newer scroll event


def schedule_redraw(delay=config.ENCODER_REDRAW_DEBOUNCE_S):
    """Coalesce a burst of redraws into one, so a fast wheel flick isn't one HTTP
    draw per detent. State itself is already updated - only the drawing waits."""
    global _redraw_task
    if _redraw_task is not None and not _redraw_task.done():
        _redraw_task.cancel()
    _redraw_task = asyncio.create_task(_delayed_redraw(delay))


async def on_encoder(delta):
    tracker.cycle_selected_label(delta, labels_store.selection_names())
    state_store.save_state(tracker)
    schedule_redraw()


async def on_device_connect():
    await busybar.force_apps_mode()
    # The bar may have rebooted while we were disconnected, so don't trust the
    # cached picture of what's on its display - repaint from scratch.
    busybar.invalidate()
    await redraw_and_broadcast()


async def _device_listener():
    print("BUSY Bar time tracker running. Press start on the bar to begin.", flush=True)
    async for kind, value, action in busybar.input_events(on_connect=on_device_connect):
        if kind == EV_ENCODER:
            await on_encoder(value)
        elif kind == EV_BUTTON and action == ACTION_PRESS:
            if value == BTN_START:
                await apply_and_broadcast(lambda t: t.toggle_start())
            elif value == BTN_OK:
                await apply_and_broadcast(lambda t: t.ok_press())


async def device_listener():
    """Outer supervisor: restart _device_listener() if anything unexpected kills it
    (button_events() itself already handles network-level reconnects). button_events()
    is designed to never return normally, but we still back off unconditionally here
    rather than assuming that invariant - a clean-but-unexpected return must not turn
    into a tight busy-loop."""
    while True:
        try:
            await _device_listener()
            print("[device_listener] button_events() ended unexpectedly, restarting in 2s...", flush=True)
        except Exception as e:
            print(f"[device_listener] unexpected error: {e!r}, restarting in 2s...", flush=True)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(device_listener())
    try:
        yield
    finally:
        task.cancel()
        if _redraw_task is not None:
            _redraw_task.cancel()
        await busybar.clear_display()
        await busybar.aclose()


app = FastAPI(lifespan=lifespan)


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/stats")
async def stats_page():
    return FileResponse(STATIC_DIR / "stats.html")


@app.get("/api/state")
async def get_state():
    return full_snapshot()


@app.post("/api/actions/start-toggle")
async def start_toggle():
    await apply_and_broadcast(lambda t: t.toggle_start())
    return full_snapshot()


@app.post("/api/actions/stop")
async def stop():
    await apply_and_broadcast(lambda t: t.stop())
    return full_snapshot()


@app.post("/api/actions/label")
async def submit_label(body: LabelBody):
    await apply_and_broadcast(lambda t: t.submit_label(body.label, body.description))
    return full_snapshot()


@app.post("/api/actions/select-label")
async def select_label(body: LabelBody):
    """Arm a label from the web UI - the mirror of turning the bar's scroll wheel."""
    await apply_and_broadcast(lambda t: t.set_selected_label(body.label))
    return full_snapshot()


@app.post("/api/actions/select-description")
async def select_description(body: DescriptionBody):
    """Arm the session's free-text note. Web-only: the bar has no way to type."""
    apply_and_publish(lambda t: t.set_selected_description(body.description))
    return full_snapshot()


@app.get("/api/labels")
async def get_labels():
    return labels_store.read_labels()


@app.post("/api/labels")
async def add_label(body: NewLabelBody):
    labels_store.add_label(body.name, body.color or None)
    await redraw_and_broadcast()
    return labels_store.read_labels()


@app.delete("/api/labels")
async def delete_label(name: str):
    labels_store.delete_label(name)
    if tracker.selected_label == name:
        # Don't leave a session armed with a label that no longer exists.
        await apply_and_broadcast(lambda t: t.set_selected_label(""))
    else:
        await redraw_and_broadcast()
    return labels_store.read_labels()


@app.get("/api/sessions")
async def get_sessions(limit: int = 20):
    return sessions_store.read_recent_sessions(limit)


@app.get("/api/sessions/all")
async def get_all_sessions():
    """Full history, uncapped - the stats page fetches this once and buckets it
    entirely client-side. Additive: does not change /api/sessions's contract."""
    return sessions_store.read_all_sessions()


@app.get("/api/events")
async def events():
    queue = broadcaster.subscribe()

    async def stream():
        try:
            yield f"data: {json.dumps(full_snapshot())}\n\n"
            while True:
                snapshot = await queue.get()
                yield f"data: {json.dumps(snapshot)}\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
