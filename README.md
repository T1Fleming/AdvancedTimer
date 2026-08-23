# AdvancedTimer

A small Python time tracker for the [BUSY Bar](https://busy.bar/). It listens
for physical button presses over the bar's WebSocket state stream, draws the
current timer on the bar's display, and records completed sessions locally.

## Requirements

- Python 3
- A BUSY Bar reachable on the local network
- The bar's API at `10.0.4.20` (change the constants in `tracker.py` if your
  device uses another address)

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Start the tracker with:

```sh
python tracker.py
```

The program switches the bar to `apps` mode when it starts, then reconnects
to the WebSocket automatically if the connection drops.

## Controls

- **START**: start, pause, or resume the current session
- **OK**: stop the session and optionally enter a label
- **Ctrl+C**: exit and clear the bar's display

While a session is running, the display shows the active elapsed time. When
paused, it shows the accumulated active duration.

## Session data

Completed sessions are appended as JSON Lines to `sessions.jsonl`. This file
is intentionally ignored by Git because it contains personal activity
timestamps. Each record includes the session start and end, total active
seconds, optional label, and active time segments.

## Project layout

- `tracker.py` - tracker application and BUSY Bar API/WebSocket client
- `pb/proto/` - protobuf definitions used by the device protocol
- `pb/*_pb2.py` - generated Python protobuf modules
- `requirements.txt` - Python dependencies
