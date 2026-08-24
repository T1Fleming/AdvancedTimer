# AdvancedTimer

A small time tracker for the [BUSY Bar](https://busy.bar/) with a local web UI. The
physical bar's buttons and any browser on your network drive the exact same session
interchangeably: press START on the bar or tap Start on your phone, either one
pauses/resumes/stops the one shared timer.

## Requirements

- Python 3
- A BUSY Bar reachable on the local network
- The bar's API at `10.0.4.20` or its Wi-Fi address, `192.168.1.220`

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create a local `.env` file to select the bar's address:

```dotenv
BUSY_BAR_IP=192.168.1.220
```

Use `BUSY_BAR_IP=10.0.4.20` for the other connection, or omit the setting to use
`10.0.4.20` by default. Shell environment variables take precedence over values in
`.env`. `WEB_HOST` (default `0.0.0.0`) and `WEB_PORT` (default `8765`) can be set the
same way.

Start the server with:

```sh
python -m app.server
```

Run this as a single process — do not use `uvicorn --reload` or multiple workers.
The timer state, the BUSY Bar WebSocket connection, and the browser update stream
are all in-process singletons; more than one process would each build its own and
fight over the device connection.

The program switches the bar to `apps` mode when it starts, then reconnects to the
device's WebSocket automatically if the connection drops.

### Using the web UI

Open `http://<this computer's LAN IP>:8765/` from any browser on the same Wi-Fi
network, including your phone. There's no login - anyone on your network can open
the page, the same trust level as the bar's own LAN API.

## Controls

Both the bar's physical buttons and the web UI control the same session:

- **START** (bar) / **Start-Pause-Resume** (web): start, pause, or resume the
  current session
- **OK** (bar) / **Stop** (web): stop the session, then enter or skip a label from
  the web UI
- Pressing **START** on the bar while a stopped session is awaiting a label skips
  labeling it and immediately starts a new session (the bar has no way to type a
  label) - **OK** does nothing in that state, since there's nothing left to stop

While a session is running, both the bar's display and the web UI show the active
elapsed time. While paused, they show the accumulated active duration. While
awaiting a label, the bar shows the final elapsed time and "SEE PHONE".

## Session data

Completed sessions are appended as JSON Lines to `sessions.jsonl`, and the web UI's
"Recent sessions" list reads from the same file. This file is intentionally ignored
by Git because it contains personal activity timestamps. Each record includes the
session start and end, total active seconds, optional label, and active time
segments.

## Testing

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
```

Tests cover the timer state machine, the sessions store, and the web API end to
end - the BUSY Bar's own HTTP/WebSocket calls are stubbed out, so no physical
device is needed to run them.

## Project layout

- `app/server.py` - FastAPI app and entry point (`python -m app.server`): runs the
  device listener and web server together
- `app/tracker_core.py` - pure timer state machine (no I/O)
- `app/busybar_client.py` - async BUSY Bar HTTP/WebSocket client and display draws
- `app/sessions_store.py` - reads/appends `sessions.jsonl`
- `app/broadcaster.py` - fans timer-state updates out to connected browsers
- `app/config.py` - environment/config loading
- `app/static/index.html` - the web UI (single page, no build step)
- `app/pb/proto/` - protobuf definitions used by the device protocol
- `app/pb/*_pb2.py` - generated Python protobuf modules
- `tests/` - unit and API tests (see Testing above)
- `requirements.txt` - Python dependencies
- `requirements-dev.txt` - adds test-only dependencies (pytest)
- `pyproject.toml` - pytest configuration
- `sessions.jsonl` - session log (repo root, gitignored)
