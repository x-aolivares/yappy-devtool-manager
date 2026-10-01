"""Ruff gate: undefined names and syntax errors must never reach a commit.

A NameError shipped in `yappy setup` (it called `_win_to_posix` while the
function is `win_to_posix`) and the command had never completed for anyone.
No test caught it, because the crash was in a step nobody exercised.

This runs ruff with the selection configured in pyproject.toml, so the gate
cannot be silently weakened by editing a test.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ["backend", "backend/tests"]


def _ruff_available() -> bool:
    return (
        shutil.which("ruff") is not None
        or subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
        ).returncode
        == 0
    )


requires_ruff = pytest.mark.skipif(
    not _ruff_available(), reason="ruff not installed (see docs/requirements-dev.txt)"
)


def test_ruff_config_is_scoped_to_correctness_rules():
    """The gate must not drift into style rules like F841.

    F841 would demand deleting `session.multiple.pf(...)` in
    `backend/cli/yappy_cli/workflow/executor.py`, a side-effect call that opens
    port-forwards. Auto-fixing it would break working tunnels.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'select = ["F821", "F811", "E9"]' in text
    for style_rule in ('"F401"', '"F541"', '"F841"'):
        assert style_rule not in text, f"{style_rule} must not be in the enforced gate"


def test_ruff_is_declared_in_dev_requirements():
    """The gate must be reproducible on a clean environment."""
    dev = (ROOT / "docs" / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "ruff" in dev, "ruff must be declared in docs/requirements-dev.txt"


@requires_ruff
def test_ruff_passes_on_package_and_tests():
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *TARGETS, "--no-cache"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "ruff found undefined names / syntax errors:\n"
        f"{result.stdout}\n{result.stderr}"
    )
