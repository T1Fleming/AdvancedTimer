import pytest

from app import config, state_store
from app.tracker_core import IDLE, PAUSED, PENDING_LABEL, RUNNING, Tracker


@pytest.fixture(autouse=True)
def isolated_state_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_PATH", tmp_path / "state.json")


def test_load_state_missing_file_returns_none():
    assert state_store.load_state() is None


def test_load_state_corrupt_file_returns_none():
    config.STATE_PATH.write_text("not valid json {{{")
    assert state_store.load_state() is None


def test_save_and_load_round_trip_while_running():
    t = Tracker()
    t.toggle_start()

    state_store.save_state(t)
    restored = state_store.load_state()

    assert restored.app_state == RUNNING
    assert restored.session_start == t.session_start
    assert restored.current_segment_start == t.current_segment_start
    assert restored.accumulated == t.accumulated
    assert restored.segments == t.segments
    assert restored.virtual_start_ts() == pytest.approx(t.virtual_start_ts())


def test_save_and_load_round_trip_while_paused():
    t = Tracker()
    t.toggle_start()
    t.toggle_start()

    state_store.save_state(t)
    restored = state_store.load_state()

    assert restored.app_state == PAUSED
    assert restored.current_segment_start is None
    assert restored.accumulated == t.accumulated
    assert restored.segments == t.segments


def test_save_and_load_round_trip_while_pending_label():
    t = Tracker()
    t.toggle_start()
    t.stop()

    state_store.save_state(t)
    restored = state_store.load_state()

    assert restored.app_state == PENDING_LABEL
    assert restored.pending == t.pending
    assert restored.snapshot() == t.snapshot()


def test_save_state_is_atomic_no_tmp_file_left_behind():
    t = Tracker()
    state_store.save_state(t)

    tmp_path = config.STATE_PATH.parent / (config.STATE_PATH.name + ".tmp")
    assert config.STATE_PATH.exists()
    assert not tmp_path.exists()


def test_load_or_new_tracker_starts_fresh_when_nothing_persisted():
    tracker = state_store.load_or_new_tracker()
    assert tracker.app_state == IDLE


def test_load_or_new_tracker_resumes_a_persisted_session():
    original = Tracker()
    original.toggle_start()
    original.toggle_start()  # paused, with a closed segment accumulated
    state_store.save_state(original)

    tracker = state_store.load_or_new_tracker()

    assert tracker.app_state == PAUSED
    assert tracker.accumulated == original.accumulated
    assert tracker.segments == original.segments


def test_load_or_new_tracker_starts_fresh_on_corrupt_file():
    config.STATE_PATH.write_text("not valid json {{{")
    tracker = state_store.load_or_new_tracker()
    assert tracker.app_state == IDLE
