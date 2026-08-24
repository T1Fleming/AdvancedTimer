"""Read/append completed sessions in sessions.jsonl."""
import json

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
