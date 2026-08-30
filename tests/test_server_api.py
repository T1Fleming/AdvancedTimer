from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import config, labels_store
from app.server import app, busybar, tracker

DRAW_METHODS = (
    "force_apps_mode", "draw_home", "draw_running", "draw_paused",
    "draw_pending_label", "clear_display", "aclose",
)


async def _empty_input_events(on_connect=None):
    return
    yield  # pragma: no cover - makes this a (non-yielding) async generator


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Isolate the on-disk stores and stub out all real BUSY Bar device I/O."""
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(config, "LABELS_PATH", tmp_path / "labels.json")
    monkeypatch.setattr(labels_store, "_cache", {"mtime": None, "labels": None})
    tracker._reset()
    for method in DRAW_METHODS:
        monkeypatch.setattr(busybar, method, AsyncMock())
    monkeypatch.setattr(busybar, "input_events", _empty_input_events)
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


def test_snapshot_carries_labels_and_selection(client):
    snapshot = client.get("/api/state").json()
    assert snapshot["selected_label"] == ""
    assert [label["name"] for label in snapshot["labels"]] == ["Cooking", "Coding", "Gaming"]
    assert all(label["color"].startswith("#") for label in snapshot["labels"])


def test_select_label_then_stop_prefills_it(client):
    assert client.post("/api/actions/select-label", json={"label": "Coding"}).json()["selected_label"] == "Coding"
    client.post("/api/actions/start-toggle")
    assert client.post("/api/actions/stop").json()["selected_label"] == "Coding"


def test_add_and_delete_label(client):
    names = [label["name"] for label in client.post("/api/labels", json={"name": "Reading"}).json()]
    assert names == ["Cooking", "Coding", "Gaming", "Reading"]

    names = [label["name"] for label in client.request("DELETE", "/api/labels", params={"name": "Coding"}).json()]
    assert names == ["Cooking", "Gaming", "Reading"]


def test_deleting_the_selected_label_clears_the_selection(client):
    client.post("/api/actions/select-label", json={"label": "Gaming"})
    client.request("DELETE", "/api/labels", params={"name": "Gaming"})
    assert client.get("/api/state").json()["selected_label"] == ""


def test_start_toggle_from_pending_files_the_selected_label(client):
    client.post("/api/actions/start-toggle")
    client.post("/api/actions/stop")
    client.post("/api/actions/select-label", json={"label": "Cooking"})

    # START while pending force-skips the web UI, filing whatever the wheel armed.
    assert client.post("/api/actions/start-toggle").json()["state"] == "RUNNING"
    assert client.get("/api/sessions").json()[0]["label"] == "Cooking"
    assert client.get("/api/state").json()["selected_label"] == ""
