"""Read/append completed sessions in sessions.jsonl."""
import json
from datetime import datetime, timezone

from . import config


def append_session(record):
    with open(config.LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_recent_sessions(limit=20):
    if not config.LOG_PATH.exists():
        return []
    lines = config.LOG_PATH.read_text().splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return list(reversed(records[-limit:]))


def total_seconds_today():
    """Active seconds logged so far today, for the bar's home screen. Never raises.

    "Today" is the local calendar day - session timestamps are stored as UTC, so
    each one is converted to local time before its date is compared.
    """
    try:
        today = datetime.now().date()
        total = 0
        for record in read_recent_sessions(limit=10_000):
            start = record.get("start")
            if not start:
                continue
            started = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if started.astimezone().date() == today:
                total += record.get("total_active_seconds", 0)
        return total
    except Exception as e:
        print(f"[sessions_store] WARNING: could not total today's sessions: {e}", flush=True)
        return 0
