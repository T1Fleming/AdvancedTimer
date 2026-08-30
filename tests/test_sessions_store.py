import json

from app import config, sessions_store


def test_read_recent_sessions_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    assert sessions_store.read_recent_sessions() == []


def test_append_and_read_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    for i in range(3):
        sessions_store.append_session({"session_id": str(i), "label": f"session {i}"})

    records = sessions_store.read_recent_sessions(limit=2)
    assert [r["label"] for r in records] == ["session 2", "session 1"]  # newest first


def test_append_session_writes_valid_jsonl_line(tmp_path, monkeypatch):
    path = tmp_path / "sessions.jsonl"
    monkeypatch.setattr(config, "LOG_PATH", path)
    sessions_store.append_session({"a": 1})
    line = path.read_text().strip()
    assert json.loads(line) == {"a": 1}


def test_reads_records_written_before_descriptions_existed(tmp_path, monkeypatch):
    """Sessions logged by an older build have no "description" key at all."""
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    legacy = {
        "session_id": "2026-08-24T06:17:43Z",
        "start": "2026-08-24T06:17:43Z",
        "end": "2026-08-24T06:17:50Z",
        "total_active_seconds": 7,
        "label": "Nothin",
        "segments": [],
    }
    sessions_store.append_session(legacy)

    record = sessions_store.read_recent_sessions()[0]
    assert record["label"] == "Nothin"
    assert record.get("description", "") == ""


def test_read_all_sessions_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    assert sessions_store.read_all_sessions() == []


def test_read_all_sessions_returns_everything_oldest_first(tmp_path, monkeypatch):
    """Unlike read_recent_sessions, not capped and not reversed."""
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    for i in range(25):
        sessions_store.append_session({"session_id": str(i), "label": f"session {i}"})

    records = sessions_store.read_all_sessions()
    assert [r["label"] for r in records] == [f"session {i}" for i in range(25)]


def test_read_all_sessions_tolerates_a_legacy_record(tmp_path, monkeypatch):
    """A record written before descriptions existed has no "description" key."""
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")
    legacy = {
        "session_id": "2026-08-24T06:17:43Z",
        "start": "2026-08-24T06:17:43Z",
        "end": "2026-08-24T06:17:50Z",
        "total_active_seconds": 7,
        "label": "Nothin",
        "segments": [],
    }
    sessions_store.append_session(legacy)

    record = sessions_store.read_all_sessions()[0]
    assert record["label"] == "Nothin"
    assert record.get("description", "") == ""
