import pytest

from app import config, sessions_store
from app.tracker_core import IDLE, PAUSED, PENDING_LABEL, RUNNING, Tracker


@pytest.fixture(autouse=True)
def isolated_sessions_log(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "sessions.jsonl")


def test_starts_idle():
    t = Tracker()
    assert t.app_state == IDLE


def test_start_toggle_cycles_running_paused_running():
    t = Tracker()
    t.toggle_start()
    assert t.app_state == RUNNING
    assert t.session_start is not None

    t.toggle_start()
    assert t.app_state == PAUSED
    assert t.current_segment_start is None
    assert len(t.segments) == 1

    t.toggle_start()
    assert t.app_state == RUNNING
    assert len(t.segments) == 1  # no new segment recorded until the next pause


def test_stop_from_idle_is_noop():
    t = Tracker()
    t.stop()
    assert t.app_state == IDLE


def test_stop_while_running_enters_pending_label():
    t = Tracker()
    t.toggle_start()
    t.stop()
    assert t.app_state == PENDING_LABEL
    assert t.pending is not None
    assert t.pending["segments"]  # closed the running segment


def test_stop_while_pending_label_is_noop():
    t = Tracker()
    t.toggle_start()
    t.stop()
    pending_before = t.pending
    t.stop()
    assert t.app_state == PENDING_LABEL
    assert t.pending is pending_before


def test_submit_label_writes_record_and_resets():
    t = Tracker()
    t.toggle_start()
    t.stop()
    t.submit_label("my label")

    assert t.app_state == IDLE
    records = sessions_store.read_recent_sessions()
    assert len(records) == 1
    assert records[0]["label"] == "my label"


def test_submit_label_empty_string_records_skip():
    t = Tracker()
    t.toggle_start()
    t.stop()
    t.submit_label("")

    records = sessions_store.read_recent_sessions()
    assert records[0]["label"] == ""


def test_submit_label_while_not_pending_is_noop():
    t = Tracker()
    t.submit_label("ignored")
    assert not config.LOG_PATH.exists()


def test_toggle_start_while_pending_label_force_skips_and_starts_new():
    t = Tracker()
    t.toggle_start()
    t.stop()
    t.toggle_start()  # force-skip the pending label + start a new session

    assert t.app_state == RUNNING
    records = sessions_store.read_recent_sessions()
    assert len(records) == 1
    assert records[0]["label"] == ""


def test_virtual_start_ts_accounts_for_accumulated():
    t = Tracker()
    t.toggle_start()  # running
    t.toggle_start()  # paused, accumulates some time
    t.toggle_start()  # running again

    assert t.virtual_start_ts() == pytest.approx(t.current_segment_start - t.accumulated, rel=1e-6)


def test_snapshot_shape_while_idle():
    t = Tracker()
    snap = t.snapshot()
    assert snap["state"] == IDLE
    assert snap["session_start"] is None
    assert "pending" not in snap


def test_snapshot_includes_pending_details():
    t = Tracker()
    t.toggle_start()
    t.stop()
    snap = t.snapshot()
    assert snap["state"] == PENDING_LABEL
    assert "total_active_seconds" in snap["pending"]
    assert "segments" in snap["pending"]
