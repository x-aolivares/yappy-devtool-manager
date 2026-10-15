"""Isolation for tests that exercise code which reaches the user's machine.

`yappy web`'s entry point is `yappy_api.run.run()`, and it does two things that
must never happen because somebody ran the test suite: read
`~/.yappy/tracker`, and (when it finds a web-owned entry whose supervisor pid is
gone) **stop that process**. Several tests call `run()` for other reasons — the
shutdown ones, the warmup ordering one — and they would be doing both.

So the tracker is redirected to a per-test temp directory for the whole suite.
Every test that cares about the tracker already monkeypatches `_TRACKER_DIR`
itself and keeps winning, because a test body's `monkeypatch.setattr` is applied
after this fixture. This changes no assertion anywhere: it removes the machine
from the blast radius.
"""

from __future__ import annotations

import pytest

from yappy_library.adapters import process_tracker


@pytest.fixture(autouse=True)
def isolated_tracker(tmp_path, monkeypatch):
    """Point the process tracker at an empty temp dir, for every test."""
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / ".yappy" / "tracker")
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})