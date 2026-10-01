"""Repository and backend resource path discovery."""

from pathlib import Path

_BACKEND_RESOURCE_DIRS = ("build", "config", "data")


def project_root() -> Path:
    """Resolve the repository root for editable installs and checkout runs.

    Python packages and backend-owned resources live below ``backend/`` while
    the Angular frontend and project metadata remain at the repository root.
    """
    package_file = Path(__file__).resolve()
    for parent in package_file.parents:
        if (parent / "pyproject.toml").is_file() and (
            parent / "backend" / "library" / "yappy_library"
        ).is_dir():
            return parent

    for parent in (Path.cwd(), *Path.cwd().parents):
        if (parent / "pyproject.toml").is_file() and (
            parent / "backend" / "library" / "yappy_library"
        ).is_dir():
            return parent

    # Legacy checkouts may still have the old root-level config directory.
    for parent in (Path.cwd(), *Path.cwd().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "config").is_dir():
            return parent

    # Preserve a predictable fallback when installed outside a source checkout.
    return Path.cwd()


def backend_root(root: Path | None = None) -> Path:
    """Return the backend source/resource directory for a repository."""
    return (root or project_root()) / "backend"


def project_config_dir(root: Path | None = None) -> Path:
    """Return the backend config directory, migrating legacy config on demand."""
    root = root or project_root()
    target = backend_root(root) / "config"
    legacy = root / "config"

    if legacy.is_dir() and (backend_root(root) / "library").is_dir():
        try:
            migrate_backend_resources(root, names=("config",))
        except FileExistsError:
            # Keep using the legacy directory rather than overwrite local values.
            return legacy

    return target if target.is_dir() or not legacy.is_dir() else legacy


def migrate_backend_resources(
    root: Path | None = None,
    *,
    names: tuple[str, ...] = _BACKEND_RESOURCE_DIRS,
) -> tuple[str, ...]:
    """Move legacy root resource directories below backend without overwriting.

    This preserves ignored local configuration and session files when an
    existing checkout upgrades to the backend-owned layout. Conflicting paths
    raise before any entries move, so the caller can resolve them explicitly.
    """
    root = root or project_root()
    backend = root / "backend"
    if not backend.is_dir():
        return ()

    candidates = [(root / name, backend / name, name) for name in names]
    pending = [
        (source, target, name)
        for source, target, name in candidates
        if source.is_dir()
    ]

    for source, target, name in pending:
        if name == "build" and target.is_dir():
            legacy_build = target / "legacy-root-build"
            if legacy_build.exists():
                raise FileExistsError(f"Resource migration would overwrite {legacy_build}")
            continue
        _check_directory_merge(source, target)

    moved: list[str] = []
    for source, target, name in pending:
        if name == "build" and target.is_dir():
            source.rename(target / "legacy-root-build")
        else:
            _merge_directory(source, target)
        moved.append(name)
    return tuple(moved)


def _check_directory_merge(source: Path, target: Path) -> None:
    if not target.exists():
        return
    if (
        source.is_dir()
        and target.is_dir()
        and not source.is_symlink()
        and not target.is_symlink()
    ):
        for child in source.iterdir():
            _check_directory_merge(child, target / child.name)
        return
    raise FileExistsError(f"Resource migration would overwrite {target}")


def _merge_directory(source: Path, target: Path) -> None:
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return

    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda path: path.name):
        destination = target / child.name
        if child.is_dir() and not child.is_symlink():
            _merge_directory(child, destination)
        else:
            child.rename(destination)
    source.rmdir()
