"""Async BUSY Bar device integration: HTTP display draws + WebSocket input events."""
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

# input_events() event kinds
EV_BUTTON, EV_ENCODER = "button", "encoder"

# --- Front display (72x16, color) layout ---------------------------------------
# A colored "spine" down the left edge carries the selected label's color, and the
# remaining 66px is split into two text rows. Every screen uses this same grammar
# so the bar reads consistently at a glance.
SPINE_W = 3
CONTENT_X = 6
CONTENT_W = 66  # x=6..71
CONTENT_RIGHT = 71
ROW1_Y = 0  # tiny font, ~5px tall
ROW2_Y = 7  # small font, ~7px tall -> ends at y=14
# When row 1 carries both a left and a right item, the left one gets this much
# room. Scrolling a long label through a box this narrow is unreadable, so screens
# that show a label on row 1 give it the full CONTENT_W instead.
ROW1_SPLIT_W = 38
# Widest label that still fits, untruncated, in row 1's right-hand slot.
ROW1_RIGHT_CHARS = 9

# --- Back display (160x80, grayscale) ------------------------------------------
# The firmware paints its own status icons (wifi, battery, charge %) down the right
# edge, so content stops at BACK_RIGHT rather than the panel's true 160px width.
BACK_LEFT = 6
BACK_RIGHT = 136
BACK_MID = (BACK_LEFT + BACK_RIGHT) // 2
BACK_CONTENT_W = BACK_RIGHT - BACK_LEFT
# Widest label that fits centered on one line in the `large` font.
BACK_LABEL_CHARS = 18

DIM = "#666666FF"
MUTED = "#888888FF"
WHITE = "#FFFFFFFF"
PALE = "#CCCCCCFF"
RULE = "#333333FF"
AMBER = "#FFAA00FF"
GREEN = "#00FF00FF"

SCROLL = {"scroll_rate": 240, "scroll_start_delay": 1500, "scroll_repeat_delay": 2500}


def label_text(name):
    return name if name else "no label"


def _ellipsize(text, limit):
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _spine(color):
    return {
        "id": "spine", "type": "rectangle",
        "x": 0, "y": 0, "width": SPINE_W, "height": 16,
        "fill": "solid", "fill_colors": [color],
        "timeout": 0,
    }


def _row1_left(text, color, width=ROW1_SPLIT_W):
    return {
        "id": "row1_left", "type": "text", "text": text,
        "font": "tiny", "color": color,
        "x": CONTENT_X, "y": ROW1_Y, "align": "top_left", "width": width,
        "timeout": 0, **SCROLL,
    }


def _row1_right(text, color):
    return {
        "id": "row1_right", "type": "text", "text": text,
        "font": "tiny", "color": color,
        "x": CONTENT_RIGHT, "y": ROW1_Y, "align": "top_right",
        "timeout": 0,
    }


def _row2_text(text, color):
    return {
        "id": "row2", "type": "text", "text": text,
        "font": "small", "color": color,
        "x": CONTENT_X, "y": ROW2_Y, "align": "top_left", "width": CONTENT_W,
        "timeout": 0, **SCROLL,
    }


def _row2_countdown(virtual_start_ts, color):
    return {
        "id": "row2_count", "type": "countdown",
        "timestamp": str(int(virtual_start_ts)),
        "direction": "time_since", "show_hours": "when_non_zero",
        "color": color,
        "x": CONTENT_X, "y": ROW2_Y, "align": "top_left",
        "timeout": 0,
    }


def _back(state_text, big_element, label, today_seconds):
    """The 160x80 grayscale back panel: header rule, big readout, label, day total.

    Colors here are luminance only - the panel is grayscale, so everything is
    white/gray rather than the front display's label colors.
    """
    return [
        {
            "id": "b_title", "type": "text", "text": "TIME TRACKER",
            "font": "tiny", "color": DIM,
            "x": BACK_LEFT, "y": 4, "align": "top_left",
            "timeout": 0, "display": "back",
        },
        {
            "id": "b_state", "type": "text", "text": state_text,
            "font": "tiny", "color": DIM,
            "x": BACK_RIGHT, "y": 4, "align": "top_right",
            "timeout": 0, "display": "back",
        },
        {
            "id": "b_rule_top", "type": "rectangle",
            "x": BACK_LEFT, "y": 16, "width": BACK_CONTENT_W, "height": 1,
            "fill": "solid", "fill_colors": [RULE],
            "timeout": 0, "display": "back",
        },
        big_element,
        {
            # No `width` here: setting one centers the *box* and left-aligns the text
            # inside it, so long names are truncated instead to keep this centered.
            "id": "b_label", "type": "text", "text": _ellipsize(label_text(label), BACK_LABEL_CHARS),
            "font": "large", "color": PALE,
            "x": BACK_MID, "y": 48, "align": "top_mid",
            "timeout": 0, "display": "back",
        },
        {
            "id": "b_rule_bottom", "type": "rectangle",
            "x": BACK_LEFT, "y": 64, "width": BACK_CONTENT_W, "height": 1,
            "fill": "solid", "fill_colors": [RULE],
            "timeout": 0, "display": "back",
        },
        {
            "id": "b_today", "type": "text",
            "text": f"TODAY {format_duration(today_seconds)}",
            "font": "small", "color": MUTED,
            "x": BACK_MID, "y": 68, "align": "top_mid",
            "timeout": 0, "display": "back",
        },
    ]


def _back_big_text(text):
    return {
        "id": "b_big", "type": "text", "text": text,
        "font": "extra_large", "color": WHITE,
        "x": BACK_MID, "y": 24, "align": "top_mid",
        "timeout": 0, "display": "back",
    }


def _back_big_countdown(virtual_start_ts):
    # A countdown element has no `font` field (see the device's CountdownElement
    # schema), so this renders at the firmware's fixed size rather than matching
    # _back_big_text's extra_large. Live ticking is worth the size difference.
    return {
        "id": "b_big_count", "type": "countdown",
        "timestamp": str(int(virtual_start_ts)),
        "direction": "time_since", "show_hours": "when_non_zero",
        "color": WHITE,
        "x": BACK_MID, "y": 24, "align": "top_mid",
        "timeout": 0, "display": "back",
    }


class BusyBarClient:
    def __init__(self):
        self._client = httpx.AsyncClient()
        # (id, type) pairs currently on the device; None means "unknown", which
        # forces the next render to clear first. See _render().
        self._drawn = None

    async def aclose(self):
        await self._client.aclose()

    def invalidate(self):
        """Forget what's on the display, so the next render clears before drawing."""
        self._drawn = None

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
                return False
            return True
        except httpx.HTTPError as e:
            print(f"[draw] FAILED: {e}", flush=True)
            return False

    async def clear_display(self):
        self._drawn = None
        try:
            await self._client.delete(
                f"{config.BASE}/display/draw", params={"application_name": config.APP_NAME}, timeout=2
            )
        except httpx.HTTPError:
            pass

    async def _render(self, elements):
        """Draw `elements`, clearing first only when the element set actually changes.

        Re-posting an id updates that element in place, so an unchanged set needs no
        clear. That matters because clearing leaves a gap with nothing of ours on
        screen, through which the device's own mode screen (apps, settings) shows -
        visible as a flash while spinning the wheel, which redraws rapidly.

        The set is compared by (id, type) because the API rejects reusing an id with
        a different type; a clear is what makes such a swap legal.
        """
        drawn = tuple((element["id"], element["type"]) for element in elements)
        if drawn != self._drawn:
            await self.clear_display()
        self._drawn = drawn if await self.draw(elements) else None

    async def draw_home(self, label, color, today_seconds):
        """Idle home screen - proof the server is alive, and the armed label."""
        await self._render([
            _spine(color),
            _row1_left("READY", DIM),
            _row1_right(format_duration(today_seconds), MUTED),
            # Chevrons signal the scroll wheel is live on this screen.
            _row2_text(f"< {label_text(label)} >", color if label else MUTED),
            *_back(
                "IDLE",
                _back_big_text(format_duration(today_seconds)),
                label,
                today_seconds,
            ),
        ])

    async def draw_running(self, virtual_start_ts, label, color, today_seconds):
        await self._render([
            _spine(color),
            # The label owns the whole row here - today's total lives on the home
            # screen and the back panel, and a long name needs the room to scroll.
            _row1_left(label_text(label).upper() if label else "TRACKING",
                       color if label else DIM, width=CONTENT_W),
            _row2_countdown(virtual_start_ts, color if label else GREEN),
            *_back(
                "RUNNING",
                _back_big_countdown(virtual_start_ts),
                label,
                today_seconds,
            ),
        ])

    async def draw_paused(self, total_seconds, label, color, today_seconds):
        await self._render([
            _spine(color),
            _row1_left("PAUSED", AMBER),
            # Fixed right-hand slot: truncate rather than overrun "PAUSED".
            _row1_right(_ellipsize(label_text(label), ROW1_RIGHT_CHARS), MUTED),
            _row2_text(format_duration(total_seconds), AMBER),
            *_back(
                "PAUSED",
                _back_big_text(format_duration(total_seconds)),
                label,
                today_seconds,
            ),
        ])

    async def draw_pending_label(self, total_seconds, label, color, today_seconds):
        """End-of-session screen: the wheel picks the label, OK files it."""
        await self._render([
            _spine(color),
            _row1_left(format_duration(total_seconds), MUTED),
            _row1_right("OK SAVE", DIM),
            _row2_text(f"< {label_text(label)} >", color if label else MUTED),
            *_back(
                "SAVE?",
                _back_big_text(format_duration(total_seconds)),
                label,
                today_seconds,
            ),
        ])

    async def input_events(self, on_connect=None):
        """Reconnect-forever async generator of real physical input events.

        Yields (EV_BUTTON, button, action) and (EV_ENCODER, delta, None).

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
                            # after connecting - not a live press, ignore it. Without
                            # this a reconnect would also spin the label selection
                            # through every replayed scroll-wheel detent.
                            continue
                        for upd in state.updates:
                            if upd.WhichOneof("state") != "input":
                                continue
                            ie = upd.input
                            kind = ie.WhichOneof("event")
                            if kind == "button_event":
                                be = ie.button_event
                                yield EV_BUTTON, be.button, be.action
                            elif kind == "encoder_event":
                                yield EV_ENCODER, ie.encoder_event.delta, None
            except (websockets.exceptions.WebSocketException, OSError) as e:
                print(f"[ws] connection error: {e}, reconnecting in 2s...", flush=True)
                await asyncio.sleep(2)
