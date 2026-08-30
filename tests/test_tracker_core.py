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


def test_to_dict_from_dict_round_trip_while_running():
    t = Tracker()
    t.toggle_start()

    restored = Tracker.from_dict(t.to_dict())

    assert restored.snapshot() == t.snapshot()
    assert restored.virtual_start_ts() == pytest.approx(t.virtual_start_ts())


def test_to_dict_from_dict_round_trip_while_paused():
    t = Tracker()
    t.toggle_start()
    t.toggle_start()

    restored = Tracker.from_dict(t.to_dict())

    assert restored.snapshot() == t.snapshot()


def test_to_dict_from_dict_round_trip_while_pending_label():
    t = Tracker()
    t.toggle_start()
    t.stop()

    restored = Tracker.from_dict(t.to_dict())

    assert restored.snapshot() == t.snapshot()
    assert restored.pending == t.pending


# --- Label selection ("modes") -------------------------------------------------

NAMES = ["", "Cooking", "Coding", "Gaming"]  # what labels_store.selection_names() returns


def test_selection_starts_on_the_none_sentinel():
    assert Tracker().selected_label == ""


def test_cycle_selected_label_wraps_in_both_directions():
    t = Tracker()
    t.cycle_selected_label(1, NAMES)
    assert t.selected_label == "Cooking"
    t.cycle_selected_label(2, NAMES)
    assert t.selected_label == "Gaming"
    t.cycle_selected_label(1, NAMES)
    assert t.selected_label == ""  # wrapped past the end
    t.cycle_selected_label(-1, NAMES)
    assert t.selected_label == "Gaming"  # wrapped back past the start


def test_cycle_from_a_label_that_no_longer_exists_restarts_at_none():
    t = Tracker()
    t.set_selected_label("Deleted")
    t.cycle_selected_label(1, NAMES)
    assert t.selected_label == "Cooking"


def test_cycle_with_no_labels_is_a_noop():
    t = Tracker()
    t.cycle_selected_label(1, [])
    assert t.selected_label == ""


def test_selection_survives_a_state_round_trip():
    t = Tracker()
    t.set_selected_label("Coding")
    t.toggle_start()

    restored = Tracker.from_dict(t.to_dict())

    assert restored.selected_label == "Coding"
    assert restored.snapshot() == t.snapshot()


def test_from_dict_defaults_selection_for_pre_label_state_files():
    """A state.json written before labels existed must still resume."""
    legacy = Tracker().to_dict()
    del legacy["selected_label"]
    assert Tracker.from_dict(legacy).selected_label == ""


def test_ok_press_stops_a_running_session():
    t = Tracker()
    t.toggle_start()
    t.ok_press()
    assert t.app_state == PENDING_LABEL


def test_ok_press_files_the_selected_label_when_pending():
    t = Tracker()
    t.set_selected_label("Gaming")
    t.toggle_start()
    t.ok_press()  # stop
    t.ok_press()  # confirm the armed label, entirely from the bar

    assert t.app_state == IDLE
    assert sessions_store.read_recent_sessions()[0]["label"] == "Gaming"


def test_filing_a_session_returns_the_wheel_to_none():
    t = Tracker()
    t.set_selected_label("Cooking")
    t.toggle_start()
    t.stop()
    t.submit_label("Cooking")
    assert t.selected_label == ""


def test_start_toggle_from_pending_files_selection_then_clears_it():
    t = Tracker()
    t.set_selected_label("Coding")
    t.toggle_start()
    t.stop()
    t.toggle_start()  # force-skip into a fresh session

    assert t.app_state == RUNNING
    assert sessions_store.read_recent_sessions()[0]["label"] == "Coding"
    assert t.selected_label == ""
