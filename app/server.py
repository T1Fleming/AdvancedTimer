#!/usr/bin/env python3
"""BUSY Bar time tracker web server.

Runs the BUSY Bar device listener (WebSocket button events, display draws) and a
local web UI in one asyncio event loop, so the physical bar and any browser on the
network are two equivalent, always-in-sync ways to drive the same session.

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

from . import config, sessions_store, state_store
from .broadcaster import StateBroadcaster
from .busybar_client import ACTION_PRESS, BTN_OK, BTN_START, BusyBarClient
from .tracker_core import IDLE, PAUSED, PENDING_LABEL, RUNNING

tracker = state_store.load_or_new_tracker()
busybar = BusyBarClient()
broadcaster = StateBroadcaster()


class LabelBody(BaseModel):
    label: str = ""


async def redraw_and_broadcast():
    """Redraw the bar's display for the current tracker state and notify SSE subscribers.

    Reused after every mutation, and again on every device (re)connect so the bar's
    display can't drift from server-side state after it loses power independently.
    """
    if tracker.app_state == RUNNING:
        await busybar.draw_running(tracker.virtual_start_ts())
    elif tracker.app_state == PAUSED:
        await busybar.draw_paused(tracker.accumulated)
    elif tracker.app_state == PENDING_LABEL:
        await busybar.draw_pending_label(tracker.pending["total_active_seconds"])
    elif tracker.app_state == IDLE:
        await busybar.clear_display()
    broadcaster.publish(tracker.snapshot())


async def apply_and_broadcast(mutation):
    mutation(tracker)
    state_store.save_state(tracker)
    await redraw_and_broadcast()


async def on_device_connect():
    await busybar.force_apps_mode()
    await redraw_and_broadcast()


async def _device_listener():
    print("BUSY Bar time tracker running. Press start on the bar to begin.", flush=True)
    async for button, action in busybar.button_events(on_connect=on_device_connect):
        if action != ACTION_PRESS:
            continue
        if button == BTN_START:
            await apply_and_broadcast(lambda t: t.toggle_start())
        elif button == BTN_OK:
            await apply_and_broadcast(lambda t: t.stop())


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
        await busybar.clear_display()
        await busybar.aclose()


app = FastAPI(lifespan=lifespan)


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state():
    return tracker.snapshot()


@app.post("/api/actions/start-toggle")
async def start_toggle():
    await apply_and_broadcast(lambda t: t.toggle_start())
    return tracker.snapshot()


@app.post("/api/actions/stop")
async def stop():
    await apply_and_broadcast(lambda t: t.stop())
    return tracker.snapshot()


@app.post("/api/actions/label")
async def submit_label(body: LabelBody):
    await apply_and_broadcast(lambda t: t.submit_label(body.label))
    return tracker.snapshot()


@app.get("/api/sessions")
async def get_sessions(limit: int = 20):
    return sessions_store.read_recent_sessions(limit)


@app.get("/api/events")
async def events():
    queue = broadcaster.subscribe()

    async def stream():
        try:
            yield f"data: {json.dumps(tracker.snapshot())}\n\n"
            while True:
                snapshot = await queue.get()
                yield f"data: {json.dumps(snapshot)}\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
