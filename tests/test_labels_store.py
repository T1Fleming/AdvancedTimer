"""Tests for the label ("mode") store, app/labels_store.py.

labels.json is meant to be edited by hand, so most of these cover the forgiving
parse path: bare strings, odd hex lengths, junk entries, and picking up edits made
outside the app without a restart.
"""
import json
import os

import pytest

from app import config, labels_store


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LABELS_PATH", tmp_path / "labels.json")
    monkeypatch.setattr(labels_store, "_cache", {"mtime": None, "labels": None})


def write_labels(raw):
    """Write labels.json directly, as a person hand-editing the file would."""
    config.LABELS_PATH.write_text(json.dumps(raw))
    # mtime has 1s granularity on some filesystems; force a distinct value so the
    # cache can't mask a real change during a fast test run.
    os.utime(config.LABELS_PATH, (0, 0))


def test_seeds_defaults_when_file_is_missing():
    assert [label["name"] for label in labels_store.read_labels()] == ["Cooking", "Coding", "Gaming"]
    assert config.LABELS_PATH.exists()
    assert [label["name"] for label in json.loads(config.LABELS_PATH.read_text())] == [
        "Cooking", "Coding", "Gaming",
    ]


def test_none_sentinel_leads_the_wheel_order():
    assert labels_store.selection_names() == ["", "Cooking", "Coding", "Gaming"]


def test_bare_string_entries_get_a_palette_color():
    write_labels(["Reading", {"name": "Coding", "color": "#3DD68C"}])
    labels = labels_store.read_labels()
    assert [label["name"] for label in labels] == ["Reading", "Coding"]
    assert labels[0]["color"] == labels_store.PALETTE[0]
    assert labels[1]["color"] == "#3DD68CFF"


@pytest.mark.parametrize("given,expected", [
    ("#f80", "#FF8800FF"),
    ("#FF8A3D", "#FF8A3DFF"),
    ("#ff8a3d80", "#FF8A3D80"),
])
def test_color_lengths_normalize_to_rrggbbaa(given, expected):
    write_labels([{"name": "X", "color": given}])
    assert labels_store.read_labels()[0]["color"] == expected


def test_unusable_color_falls_back_to_palette_without_losing_the_label():
    write_labels([{"name": "X", "color": "not a color"}])
    labels = labels_store.read_labels()
    assert labels[0]["name"] == "X"
    assert labels[0]["color"] == labels_store.PALETTE[0]


def test_malformed_entries_are_skipped_not_fatal():
    write_labels([{"name": "Keep"}, 42, {"color": "#fff"}, {"name": "   "}, None, "Also"])
    assert [label["name"] for label in labels_store.read_labels()] == ["Keep", "Also"]


def test_unreadable_file_falls_back_to_defaults():
    config.LABELS_PATH.write_text("{ not json")
    assert [label["name"] for label in labels_store.read_labels()] == ["Cooking", "Coding", "Gaming"]


def test_non_array_json_falls_back_to_defaults():
    write_labels({"labels": ["Cooking"]})
    assert [label["name"] for label in labels_store.read_labels()] == ["Cooking", "Coding", "Gaming"]


def test_hand_edits_are_picked_up_without_a_restart():
    assert labels_store.label_names() == ["Cooking", "Coding", "Gaming"]
    write_labels(["Woodworking"])
    assert labels_store.label_names() == ["Woodworking"]


def test_add_and_delete_round_trip():
    labels_store.add_label("Reading", "#38BDF8")
    assert labels_store.label_names() == ["Cooking", "Coding", "Gaming", "Reading"]
    assert labels_store.color_for("Reading") == "#38BDF8FF"

    labels_store.delete_label("Coding")
    assert labels_store.label_names() == ["Cooking", "Gaming", "Reading"]
    # The write actually landed on disk, not just in the cache.
    assert [label["name"] for label in json.loads(config.LABELS_PATH.read_text())] == [
        "Cooking", "Gaming", "Reading",
    ]


def test_add_rejects_case_insensitive_duplicates_and_blanks():
    labels_store.add_label("cooking")
    labels_store.add_label("   ")
    assert labels_store.label_names() == ["Cooking", "Coding", "Gaming"]


def test_add_without_a_color_assigns_one_from_the_palette():
    labels_store.add_label("Reading")
    assert labels_store.color_for("Reading") == labels_store.PALETTE[3]


def test_color_for_unknown_or_none_is_the_none_color():
    assert labels_store.color_for("") == labels_store.NONE_COLOR
    assert labels_store.color_for("Deleted Long Ago") == labels_store.NONE_COLOR
