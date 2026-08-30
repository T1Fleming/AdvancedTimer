"""Pure timer state machine - no I/O, no asyncio, no device/display knowledge.

States: IDLE -> RUNNING <-> PAUSED -> PENDING_LABEL -> IDLE.

Also owns `selected_label`, the label armed for the current session ("" = none).
It can be set at any point - before starting, mid-session, or while a finished
session waits to be filed - and resets to "" once a session is filed. The list of
available labels lives in labels_store and is passed into cycle_selected_label(),
so this module stays free of I/O.
"""
import time
from datetime import datetime, timezone

from . import sessions_store

IDLE, RUNNING, PAUSED, PENDING_LABEL = "IDLE", "RUNNING", "PAUSED", "PENDING_LABEL"


def now_iso(ts=None):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_duration(total_seconds):
    total_seconds = int(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class Tracker:
    def __init__(self):
        self.app_state = IDLE
        self.session_start = None
        self.segments = []
        self.current_segment_start = None
        self.accumulated = 0.0
        self.pending = None  # finalized session data, set while PENDING_LABEL
        self.selected_label = ""  # armed label, "" = none; picked by wheel or web UI

    def to_dict(self):
        return {
            "app_state": self.app_state,
            "session_start": self.session_start,
            "segments": self.segments,
            "current_segment_start": self.current_segment_start,
            "accumulated": self.accumulated,
            "pending": self.pending,
            "selected_label": self.selected_label,
        }

    @classmethod
    def from_dict(cls, data):
        t = cls()
        t.app_state = data["app_state"]
        t.session_start = data["session_start"]
        t.segments = data["segments"]
        t.current_segment_start = data["current_segment_start"]
        t.accumulated = data["accumulated"]
        t.pending = data["pending"]
        # .get: a state.json written before labels existed must still resume.
        t.selected_label = data.get("selected_label", "")
        return t

    def virtual_start_ts(self):
        """Timestamp the on-device countdown element should count up from."""
        return (self.current_segment_start or time.time()) - self.accumulated

    def set_selected_label(self, name):
        self.selected_label = name or ""

    def cycle_selected_label(self, delta, names):
        """Move the selection `delta` steps through `names`, wrapping around.

        `names` is passed in rather than read from disk so this module stays pure;
        names[0] is expected to be the "" none sentinel.
        """
        if not names:
            return
        try:
            index = names.index(self.selected_label)
        except ValueError:
            index = 0  # selection was deleted out from under us
        self.selected_label = names[(index + delta) % len(names)]

    def ok_press(self):
        """The bar's OK button: confirm the armed label when one is pending, else stop."""
        if self.app_state == PENDING_LABEL:
            self.submit_label(self.selected_label)
        else:
            self.stop()

    def toggle_start(self):
        if self.app_state == PENDING_LABEL:
            # Force-skip: file this session under whatever is armed (nothing, unless
            # the wheel was used), then start a fresh one immediately.
            self._finalize_label(self.selected_label)
            self.selected_label = ""
            self._start_new_session()
            return
        if self.app_state == IDLE:
            self._start_new_session()
        elif self.app_state == RUNNING:
            self._close_segment()
            self.app_state = PAUSED
        elif self.app_state == PAUSED:
            self.current_segment_start = time.time()
            self.app_state = RUNNING

    def stop(self):
        if self.app_state not in (RUNNING, PAUSED):
            return
        if self.app_state == RUNNING:
            self._close_segment()
        self.pending = {
            "session_start": self.session_start,
            "end_ts": time.time(),
            "total_active_seconds": self.accumulated,
            "segments": self.segments,
        }
        self.app_state = PENDING_LABEL

    def submit_label(self, label):
        if self.app_state != PENDING_LABEL:
            return
        self._finalize_label(label)
        self._reset()

    def snapshot(self):
        data = {
            "state": self.app_state,
            "session_start": now_iso(self.session_start) if self.session_start else None,
            "current_segment_start": now_iso(self.current_segment_start) if self.current_segment_start else None,
            "accumulated_seconds": self.accumulated,
            "selected_label": self.selected_label,
        }
        if self.app_state == PENDING_LABEL and self.pending:
            data["pending"] = {
                "session_start": now_iso(self.pending["session_start"]),
                "end": now_iso(self.pending["end_ts"]),
                "total_active_seconds": round(self.pending["total_active_seconds"]),
                "segments": [
                    {
                        "start": now_iso(seg["start_ts"]),
                        "end": now_iso(seg["end_ts"]),
                        "duration_seconds": round(seg["end_ts"] - seg["start_ts"]),
                    }
                    for seg in self.pending["segments"]
                ],
            }
        return data

    def _start_new_session(self):
        now = time.time()
        self.session_start = now
        self.segments = []
        self.accumulated = 0.0
        self.current_segment_start = now
        self.pending = None
        self.app_state = RUNNING

    def _close_segment(self):
        now = time.time()
        self.segments.append({"start_ts": self.current_segment_start, "end_ts": now})
        self.accumulated += now - self.current_segment_start
        self.current_segment_start = None

    def _finalize_label(self, label):
        p = self.pending
        record = {
            "session_id": now_iso(p["session_start"]),
            "start": now_iso(p["session_start"]),
            "end": now_iso(p["end_ts"]),
            "total_active_seconds": round(p["total_active_seconds"]),
            "label": (label or "").strip(),
            "segments": [
                {
                    "start": now_iso(seg["start_ts"]),
                    "end": now_iso(seg["end_ts"]),
                    "duration_seconds": round(seg["end_ts"] - seg["start_ts"]),
                }
                for seg in p["segments"]
            ],
        }
        sessions_store.append_session(record)

    def _reset(self):
        self.app_state = IDLE
        self.session_start = None
        self.segments = []
        self.current_segment_start = None
        self.accumulated = 0.0
        self.pending = None
        self.selected_label = ""  # a filed session always returns the wheel to (none)
