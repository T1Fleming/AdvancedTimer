from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import config
from app.server import app, busybar, tracker


async def _empty_button_events(on_connect=None):
    return
    yield  # pragma: no cover - makes this a (non-yielding) async generator


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Isolate sessions.jsonl/state.json and stub out all real BUSY Bar device I/O."""
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    tracker._reset()
    for method in ("force_apps_mode", "draw_running", "draw_paused", "draw_pending_label", "clear_display", "aclose"):
        monkeypatch.setattr(busybar, method, AsyncMock())
    monkeypatch.setattr(busybar, "button_events", _empty_button_events)
    yield
    tracker._reset()


@pytest.fixture
def client(isolate):
    with TestClient(app) as c:
        yield c


def test_get_state_starts_idle(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json()["state"] == "IDLE"


def test_full_start_pause_resume_stop_label_flow(client):
    assert client.post("/api/actions/start-toggle").json()["state"] == "RUNNING"
    assert client.post("/api/actions/start-toggle").json()["state"] == "PAUSED"
    assert client.post("/api/actions/start-toggle").json()["state"] == "RUNNING"
    assert client.post("/api/actions/stop").json()["state"] == "PENDING_LABEL"
    assert client.post("/api/actions/label", json={"label": "test"}).json()["state"] == "IDLE"

    sessions = client.get("/api/sessions").json()
    assert sessions[0]["label"] == "test"


def test_stop_is_noop_while_pending_label(client):
    client.post("/api/actions/start-toggle")
    client.post("/api/actions/stop")
    r = client.post("/api/actions/stop")
    assert r.json()["state"] == "PENDING_LABEL"


def test_start_toggle_force_skips_pending_label(client):
    client.post("/api/actions/start-toggle")
    client.post("/api/actions/stop")
    r = client.post("/api/actions/start-toggle")
    assert r.json()["state"] == "RUNNING"

    sessions = client.get("/api/sessions").json()
    assert sessions[0]["label"] == ""


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
