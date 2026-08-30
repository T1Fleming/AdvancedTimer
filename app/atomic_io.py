"""Atomic JSON file writes shared by the state and label stores.

Write to a sibling .tmp file, fsync it, then os.replace() into place - a reader
either sees the whole previous file or the whole new one, never a torn write.
"""
import json
import os


def write_json_atomic(path, data):
    """Atomically replace `path` with `data` as JSON. Raises on failure."""
    tmp_path = path.parent / (path.name + ".tmp")
    with open(tmp_path, "w") as f:
        f.write(json.dumps(data))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
