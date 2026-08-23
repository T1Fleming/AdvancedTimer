#!/usr/bin/env python3
"""BUSY Bar time tracker.

Listens to the BUSY Bar's WebSocket state stream (protobuf-encoded, see
pb/proto/input.proto) for real physical button-press events. START toggles
pause/resume; OK ends the session and logs it to sessions.jsonl.

Requires the device to be in "apps" mode (POST /api/input?key=apps is called
automatically at startup) - this is the only mode that both streams button
events AND allows our display draws through, since it doesn't run a native
BUSY/CUSTOM focus-timer session competing for display priority. See the plan
for the full investigation behind this.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import websocket

sys.path.insert(0, str(Path(__file__).parent / "pb"))
import state_pb2  # noqa: E402


def load_env_file():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()
BUSY_BAR_IP = os.getenv("BUSY_BAR_IP", "10.0.4.20")
BASE = f"http://{BUSY_BAR_IP}/api"
WS_URL = f"ws://{BUSY_BAR_IP}/api/status/ws"
APP_NAME = "time_tracker"
PRIORITY = 90
LOG_PATH = Path(__file__).parent / "sessions.jsonl"
MAX_EVENT_AGE_MS = 2000

# Button enum values from pb/proto/input.proto: OK=0, BACK=1, START=2
BTN_OK, BTN_BACK, BTN_START = 0, 1, 2
ACTION_PRESS, ACTION_RELEASE = 0, 1


def force_apps_mode():
    try:
        requests.post(f"{BASE}/input", params={"key": "apps"}, timeout=2)
    except requests.RequestException:
        pass


def draw(elements):
    try:
        r = requests.post(
            f"{BASE}/display/draw",
            json={"application_name": APP_NAME, "priority": PRIORITY, "elements": elements},
            timeout=2,
        )
        if r.status_code != 200:
            print(f"[draw] WARNING status={r.status_code} body={r.text}", flush=True)
    except requests.RequestException as e:
        print(f"[draw] FAILED: {e}", flush=True)


def clear_display():
    try:
        requests.delete(f"{BASE}/display/draw", params={"application_name": APP_NAME}, timeout=2)
    except requests.RequestException:
        pass


def draw_running(virtual_start_ts):
    clear_display()  # the API rejects reusing an id with a different element type
    draw([{
        "id": "elapsed", "type": "countdown",
        "timestamp": str(int(virtual_start_ts)),
        "direction": "time_since",
        "show_hours": "when_non_zero",
        "color": "#00FF00FF",
        "x": 36, "y": 8, "align": "center", "timeout": 0,
    }])


def format_duration(total_seconds):
    total_seconds = int(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def draw_paused(total_seconds):
    clear_display()  # the API rejects reusing an id with a different element type
    draw([{
        "id": "elapsed", "type": "text",
        "text": f"PAUSED {format_duration(total_seconds)}",
        "font": "small", "color": "#FFAA00FF",
        "x": 36, "y": 8, "align": "center", "width": 72,
        "timeout": 0,
    }])


def now_iso(ts=None):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Tracker:
    def __init__(self):
        self.app_state = "IDLE"  # IDLE | RUNNING | PAUSED
        self.session_start = None
        self.segments = []
        self.current_segment_start = None
        self.accumulated = 0.0

    def on_start_press(self):
        now = time.time()
        if self.app_state == "IDLE":
            self.session_start = now
            self.segments = []
            self.accumulated = 0.0
            self.current_segment_start = now
            self.app_state = "RUNNING"
            print(f"[{now_iso()}] session started", flush=True)
            draw_running(now - self.accumulated)
        elif self.app_state == "RUNNING":
            duration = now - self.current_segment_start
            self.segments.append({"start_ts": self.current_segment_start, "end_ts": now})
            self.accumulated += duration
            self.current_segment_start = None
            self.app_state = "PAUSED"
            print(f"[{now_iso()}] paused (active so far: {format_duration(self.accumulated)})", flush=True)
            draw_paused(self.accumulated)
        elif self.app_state == "PAUSED":
            self.current_segment_start = now
            self.app_state = "RUNNING"
            print(f"[{now_iso()}] resumed", flush=True)
            draw_running(now - self.accumulated)

    def on_ok_press(self):
        if self.app_state == "IDLE":
            return
        now = time.time()
        if self.app_state == "RUNNING" and self.current_segment_start is not None:
            duration = now - self.current_segment_start
            self.segments.append({"start_ts": self.current_segment_start, "end_ts": now})
            self.accumulated += duration
            self.current_segment_start = None

        clear_display()
        total_active = self.accumulated
        session_start = self.session_start
        segments = self.segments
        print(f"[{now_iso()}] session stopped - total active: {format_duration(total_active)}", flush=True)

        try:
            label = input("Session label (enter to skip): ").strip()
        except EOFError:
            label = ""

        record = {
            "session_id": now_iso(session_start),
            "start": now_iso(session_start),
            "end": now_iso(now),
            "total_active_seconds": round(total_active),
            "label": label,
            "segments": [
                {
                    "start": now_iso(seg["start_ts"]),
                    "end": now_iso(seg["end_ts"]),
                    "duration_seconds": round(seg["end_ts"] - seg["start_ts"]),
                }
                for seg in segments
            ],
        }
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"[{now_iso()}] logged to {LOG_PATH}", flush=True)

        self.app_state = "IDLE"
        self.session_start = None
        self.segments = []
        self.accumulated = 0.0
        self.current_segment_start = None

    def handle_button_event(self, button, action):
        if action != ACTION_PRESS:
            return
        if button == BTN_START:
            self.on_start_press()
        elif button == BTN_OK:
            self.on_ok_press()


def run():
    tracker = Tracker()
    force_apps_mode()
    print("BUSY Bar time tracker running. Press start on the bar to begin. Ctrl+C to quit.", flush=True)

    while True:
        try:
            ws = websocket.create_connection(WS_URL, timeout=10)
            ws.settimeout(15)
            ws.send(json.dumps({"enable": True}))
            print("[ws] connected", flush=True)
            while True:
                opcode, payload = ws.recv_data()
                if opcode != websocket.ABNF.OPCODE_BINARY:
                    continue
                try:
                    state = state_pb2.State()
                    state.ParseFromString(payload)
                except Exception:
                    continue
                age_ms = int(time.time() * 1000) - state.timestamp
                if age_ms > MAX_EVENT_AGE_MS:
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
                    tracker.handle_button_event(be.button, be.action)
        except (websocket.WebSocketException, OSError) as e:
            print(f"[ws] connection error: {e}, reconnecting in 2s...", flush=True)
            time.sleep(2)
        except KeyboardInterrupt:
            raise


def main():
    try:
        run()
    except KeyboardInterrupt:
        print("\nExiting, clearing display...", flush=True)
        clear_display()
        sys.exit(0)


if __name__ == "__main__":
    main()
