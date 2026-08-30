"""Persist/restore the in-progress Tracker so a crash or power loss can be resumed."""
import json

from . import config
from .atomic_io import write_json_atomic
from .tracker_core import Tracker


def save_state(tracker):
    """Best-effort atomic write of the full in-progress Tracker state. Never raises."""
    try:
        write_json_atomic(config.STATE_PATH, tracker.to_dict())
    except Exception as e:
        print(f"[state_store] WARNING: failed to save state: {e}", flush=True)


def load_state():
    """Return a restored Tracker, or None if there's nothing (or nothing valid) to resume."""
    if not config.STATE_PATH.exists():
        return None
    try:
        data = json.loads(config.STATE_PATH.read_text())
        return Tracker.from_dict(data)
    except Exception as e:
        print(f"[state_store] WARNING: ignoring corrupt state file ({e}), starting fresh", flush=True)
        return None


def load_or_new_tracker():
    """The exact fallback logic used at server startup: resume if possible, else fresh."""
    tracker = load_state()
    if tracker is not None:
        print(f"[state_store] resumed persisted session (state={tracker.app_state})", flush=True)
        return tracker
    return Tracker()
