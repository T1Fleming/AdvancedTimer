"""The set of session labels ("modes"), stored in a hand-editable labels.json.

The file is a JSON array. Entries are either an object or a bare string, so it
stays pleasant to edit by hand:

    [
      {"name": "Cooking", "color": "#FF8A3D"},
      "Reading"
    ]

A bare string (or a missing/unparseable color) gets a color assigned from PALETTE
by position. Parsing is deliberately forgiving - a malformed entry is skipped with
a warning rather than taking down the whole label list, mirroring how
state_store.load_state() treats a corrupt state file.

Reads are cached against the file's mtime, so editing labels.json by hand takes
effect without restarting the server.
"""
import json
import re

from . import config
from .atomic_io import write_json_atomic

DEFAULT_LABEL_NAMES = ["Cooking", "Coding", "Gaming"]

# Bright, well-separated colors - these drive the BUSY Bar's LEDs, where anything
# dark or low-saturation reads as "off".
PALETTE = [
    "#FF8A3DFF",  # orange
    "#3DD68CFF",  # green
    "#A78BFAFF",  # purple
    "#38BDF8FF",  # sky
    "#F472B6FF",  # pink
    "#FACC15FF",  # yellow
    "#F87171FF",  # red
    "#34D399FF",  # teal
]

# The "no label" sentinel: an empty name, always first in the wheel order.
NONE_NAME = ""
NONE_COLOR = "#555555FF"

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

_cache = {"mtime": None, "labels": None}


def _palette_color(index):
    return PALETTE[index % len(PALETTE)]


def normalize_color(value, index=0):
    """Accept #RGB / #RRGGBB / #RRGGBBAA and return the #RRGGBBAA the device wants.

    Anything unusable falls back to this entry's palette color, so a typo in a
    hand-edited file costs you the color you meant, not the label itself.
    """
    if not isinstance(value, str) or not _HEX_RE.match(value.strip()):
        return _palette_color(index)
    digits = value.strip()[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    if len(digits) == 6:
        digits += "FF"
    return "#" + digits.upper()


def _parse(raw, index):
    """One raw JSON entry -> {"name", "color"}, or None if it's unusable."""
    if isinstance(raw, str):
        name, color = raw, None
    elif isinstance(raw, dict):
        name, color = raw.get("name"), raw.get("color")
    else:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    return {"name": name.strip(), "color": normalize_color(color, index)}


def _default_labels():
    return [
        {"name": name, "color": _palette_color(i)}
        for i, name in enumerate(DEFAULT_LABEL_NAMES)
    ]


def _write(labels):
    write_json_atomic(config.LABELS_PATH, labels)
    _cache["mtime"] = config.LABELS_PATH.stat().st_mtime
    _cache["labels"] = labels


def read_labels():
    """The current ordered labels, seeding the file on first use. Never raises."""
    if not config.LABELS_PATH.exists():
        labels = _default_labels()
        try:
            _write(labels)
        except Exception as e:
            print(f"[labels_store] WARNING: could not seed labels file: {e}", flush=True)
        return labels

    try:
        mtime = config.LABELS_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if mtime is not None and mtime == _cache["mtime"] and _cache["labels"] is not None:
        return _cache["labels"]

    try:
        raw = json.loads(config.LABELS_PATH.read_text())
    except Exception as e:
        print(f"[labels_store] WARNING: ignoring unreadable labels file ({e})", flush=True)
        return _cache["labels"] or _default_labels()

    if not isinstance(raw, list):
        print("[labels_store] WARNING: labels.json is not a JSON array, ignoring", flush=True)
        return _cache["labels"] or _default_labels()

    labels, seen = [], set()
    for i, entry in enumerate(raw):
        parsed = _parse(entry, i)
        if parsed is None:
            print(f"[labels_store] WARNING: skipping malformed label entry {entry!r}", flush=True)
            continue
        if parsed["name"].casefold() in seen:
            continue
        seen.add(parsed["name"].casefold())
        labels.append(parsed)

    _cache["mtime"] = mtime
    _cache["labels"] = labels
    return labels


def label_names():
    return [label["name"] for label in read_labels()]


def selection_names():
    """Wheel order: the "no label" sentinel first, then every configured label."""
    return [NONE_NAME] + label_names()


def color_for(name):
    if not name:
        return NONE_COLOR
    for label in read_labels():
        if label["name"] == name:
            return label["color"]
    return NONE_COLOR  # a label that was deleted after a session was logged


def add_label(name, color=None):
    """Append a label unless the name is already taken (case-insensitively)."""
    name = (name or "").strip()
    labels = list(read_labels())
    if not name or any(label["name"].casefold() == name.casefold() for label in labels):
        return labels
    labels.append({"name": name, "color": normalize_color(color, len(labels))})
    _write(labels)
    return labels


def delete_label(name):
    labels = [label for label in read_labels() if label["name"] != name]
    _write(labels)
    return labels
