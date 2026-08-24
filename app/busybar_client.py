"""Async BUSY Bar device integration: HTTP display draws + WebSocket button events."""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

from . import config
from .tracker_core import format_duration

sys.path.insert(0, str(Path(__file__).parent / "pb"))
import state_pb2  # noqa: E402

# Button enum values from pb/proto/input.proto: OK=0, BACK=1, START=2
BTN_OK, BTN_BACK, BTN_START = 0, 1, 2
ACTION_PRESS, ACTION_RELEASE = 0, 1


class BusyBarClient:
    def __init__(self):
        self._client = httpx.AsyncClient()

    async def aclose(self):
        await self._client.aclose()

    async def force_apps_mode(self):
        try:
            await self._client.post(f"{config.BASE}/input", params={"key": "apps"}, timeout=2)
        except httpx.HTTPError:
            pass

    async def draw(self, elements):
        try:
            r = await self._client.post(
                f"{config.BASE}/display/draw",
                json={"application_name": config.APP_NAME, "priority": config.PRIORITY, "elements": elements},
                timeout=2,
            )
            if r.status_code != 200:
                print(f"[draw] WARNING status={r.status_code} body={r.text}", flush=True)
        except httpx.HTTPError as e:
            print(f"[draw] FAILED: {e}", flush=True)

    async def clear_display(self):
        try:
            await self._client.delete(
                f"{config.BASE}/display/draw", params={"application_name": config.APP_NAME}, timeout=2
            )
        except httpx.HTTPError:
            pass

    async def draw_running(self, virtual_start_ts):
        await self.clear_display()  # the API rejects reusing an id with a different element type
        await self.draw([{
            "id": "elapsed", "type": "countdown",
            "timestamp": str(int(virtual_start_ts)),
            "direction": "time_since",
            "show_hours": "when_non_zero",
            "color": "#00FF00FF",
            "x": 36, "y": 8, "align": "center", "timeout": 0,
        }])

    async def draw_paused(self, total_seconds):
        await self.clear_display()  # the API rejects reusing an id with a different element type
        await self.draw([{
            "id": "elapsed", "type": "text",
            "text": f"PAUSED {format_duration(total_seconds)}",
            "font": "small", "color": "#FFAA00FF",
            "x": 36, "y": 8, "align": "center", "width": 72,
            "timeout": 0,
        }])

    async def draw_pending_label(self, total_seconds):
        await self.clear_display()  # the API rejects reusing an id with a different element type
        await self.draw([
            {
                "id": "elapsed", "type": "text",
                "text": format_duration(total_seconds),
                "font": "bold", "color": "#FFFFFFFF",
                "x": 36, "y": 4, "align": "center", "width": 72,
                "timeout": 0,
            },
            {
                "id": "label_hint", "type": "text",
                "text": "SEE PHONE",
                "font": "small", "color": "#FFAA00FF",
                "x": 36, "y": 12, "align": "center", "width": 72,
                "timeout": 0,
            },
        ])

    async def button_events(self, on_connect=None):
        """Reconnect-forever async generator yielding (button, action) for real presses.

        Calls `on_connect()` after every successful (re)connection + subscribe, before
        the per-message loop starts - including the very first connection, so callers
        no longer need a separate one-time setup call before entering this loop.
        """
        while True:
            try:
                async with websockets.connect(config.WS_URL, open_timeout=10) as ws:
                    await ws.send(json.dumps({"enable": True}))
                    print("[ws] connected", flush=True)
                    if on_connect is not None:
                        await on_connect()
                    async for message in ws:
                        if not isinstance(message, (bytes, bytearray)):
                            continue
                        try:
                            state = state_pb2.State()
                            state.ParseFromString(message)
                        except Exception:
                            continue
                        age_ms = int(time.time() * 1000) - state.timestamp
                        if age_ms > config.MAX_EVENT_AGE_MS:
                            # Stale event, likely part of the backlog replayed right
                            # after connecting - not a live press, ignore it.
                            continue
                        for upd in state.updates:
                            if upd.WhichOneof("state") != "input":
                                continue
                            ie = upd.input
                            if ie.WhichOneof("event") != "button_event":
                                continue
                            be = ie.button_event
                            yield be.button, be.action
            except (websockets.exceptions.WebSocketException, OSError) as e:
                print(f"[ws] connection error: {e}, reconnecting in 2s...", flush=True)
                await asyncio.sleep(2)
