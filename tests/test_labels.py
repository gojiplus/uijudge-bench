"""Tests for label-file loading behavior."""

from pathlib import Path

import pytest

from uijudge.labels import read_items


def test_read_items_rejects_missing_file(tmp_path: Path) -> None:
    """A missing dataset must fail loudly instead of masquerading as zero items."""
    missing = tmp_path / "missing.jsonl"

    with pytest.raises(FileNotFoundError, match="labels file not found"):
        read_items(missing)


def test_read_items_accepts_empty_file(tmp_path: Path) -> None:
    """An existing empty label file is a valid zero-item dataset."""
    labels = tmp_path / "items.jsonl"
    labels.touch()

    assert read_items(labels) == []
