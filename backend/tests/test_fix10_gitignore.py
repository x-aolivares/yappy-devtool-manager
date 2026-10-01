from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_gitignore_covers_all_env_files_with_example_negation():
    content = (ROOT / ".gitignore").read_text()
    assert "config/env.*" in content
    assert "!config/env.*.example" in content
    assert "backend/config/env.*" in content
    assert "!backend/config/env.*.example" in content
    assert "backend/build/" in content
    assert "backend/data/" in content
    assert "config/env.base" not in content
    assert "config/env.dev" not in content
    assert "config/env.qa" not in content


def test_gitignore_example_files_stay_visible_to_git():
    base = ROOT / ".gitignore"
    lines = base.read_text().splitlines()
    idx = lines.index("config/env.*")
    assert lines[idx + 1] == "!config/env.*.example"
    backend_idx = lines.index("backend/config/env.*")
    assert lines[backend_idx + 1] == "!backend/config/env.*.example"
