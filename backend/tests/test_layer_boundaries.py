"""Keep backend imports aligned with the library/API/CLI boundaries."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def _imported_top_level_packages(source_root: Path) -> set[str]:
    imported = set()
    for source in source_root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".", 1)[0])
    return imported


def test_backend_layers_exist_as_separate_packages():
    assert (BACKEND / "library" / "yappy_library" / "__init__.py").is_file()
    assert (BACKEND / "api" / "yappy_api" / "__init__.py").is_file()
    assert (BACKEND / "cli" / "yappy_cli" / "__init__.py").is_file()


def test_library_does_not_depend_on_api_or_cli():
    imports = _imported_top_level_packages(BACKEND / "library" / "yappy_library")
    assert "yappy_api" not in imports
    assert "yappy_cli" not in imports


def test_api_does_not_depend_on_cli():
    imports = _imported_top_level_packages(BACKEND / "api" / "yappy_api")
    assert "yappy_cli" not in imports


def test_cli_depends_on_api_only_to_start_the_web_server():
    cli_root = BACKEND / "cli" / "yappy_cli"
    importers = []
    for source in cli_root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        if any(
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and node.module.split(".", 1)[0] == "yappy_api"
            for node in ast.walk(tree)
        ):
            importers.append(source.relative_to(cli_root).as_posix())

    assert importers == ["cli.py"]
