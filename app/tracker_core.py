"""Pure timer state machine - no I/O, no asyncio, no device/display knowledge.

States: IDLE -> RUNNING <-> PAUSED -> PENDING_LABEL -> IDLE.
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

    def virtual_start_ts(self):
        """Timestamp the on-device countdown element should count up from."""
        return (self.current_segment_start or time.time()) - self.accumulated

    def toggle_start(self):
        if self.app_state == PENDING_LABEL:
            self._finalize_label("")  # force-skip: label this session as unlabeled...
            self._start_new_session()  # ...and start a fresh one immediately
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
