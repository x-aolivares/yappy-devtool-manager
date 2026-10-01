"""Path discovery shared by the CLI and HTTP API."""

from pathlib import Path


def project_root() -> Path:
    """Resolve the repository root for editable installs and checkout runs.

    The source packages live below ``backend/`` while project-owned resources
    (``config/``, ``devkit/`` and ``frontend/``) remain at the repository root.
    For a non-editable install, fall back to a checkout found from the current
    working directory.
    """
    package_file = Path(__file__).resolve()
    for parent in package_file.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "config").is_dir():
            return parent

    for parent in (Path.cwd(), *Path.cwd().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "config").is_dir():
            return parent

    # Preserve a predictable fallback when installed outside a source checkout.
    return Path.cwd()
