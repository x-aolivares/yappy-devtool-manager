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

import threading

import pytest

from yappy_api import shutdown
from yappy_library.adapters import process_tracker


@pytest.fixture(autouse=True)
def isolated_tracker(tmp_path, monkeypatch):
    """Point the process tracker at an empty temp dir, for every test."""
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / ".yappy" / "tracker")
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})


@pytest.fixture(autouse=True)
def clean_shutdown_flag(monkeypatch):
    """A clean shutdown flag for every test.

    ``yappy_api.shutdown`` holds a process-global ``Event`` that, once set, makes
    both SSE streams close on their first turn. That is exactly the kind of state
    a single test must not leave behind: the suite's later stream tests would all
    close instantly and fail, with the failure showing up far from the test that
    lit it — and, worse, a test asserting "the stream closes" would pass for the
    wrong reason.
    """
    monkeypatch.setattr(shutdown, "_flag", threading.Event())