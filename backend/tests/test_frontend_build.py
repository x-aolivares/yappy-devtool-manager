from pathlib import Path

from yappy_cli.cli import _frontend_needs_build


def _make_frontend(tmp_path: Path) -> Path:
    fe = tmp_path / "frontend"
    (fe / "src").mkdir(parents=True)
    (fe / "package.json").write_text("{}")
    (fe / "src" / "main.ts").write_text("console.log(1)")
    (fe / "dist" / "browser").mkdir(parents=True)
    return fe


def _stamp(frontend: Path, index: int, sources: int) -> None:
    """Fijar mtime: index y fuentes; fuentes anterior al index si sources < index."""
    import os

    index_path = frontend / "dist" / "browser" / "index.html"
    os.utime(index_path, (index, index))
    for f in [frontend / "package.json", frontend / "src" / "main.ts"]:
        os.utime(f, (sources, sources))


def test_frontend_needs_build_when_dist_missing(tmp_path):
    fe = _make_frontend(tmp_path)
    assert (fe / "dist" / "browser" / "index.html").exists() is False
    assert _frontend_needs_build(fe) is True


def test_frontend_needs_build_when_source_newer_than_dist(tmp_path):
    fe = _make_frontend(tmp_path)
    (fe / "dist" / "browser" / "index.html").write_text("index")
    _stamp(fe, index=1000, sources=2000)  # fuentes (2000) más nuevas que index
    assert _frontend_needs_build(fe) is True


def test_frontend_needs_build_false_when_dist_is_fresh(tmp_path):
    fe = _make_frontend(tmp_path)
    (fe / "dist" / "browser" / "index.html").write_text("index")
    _stamp(fe, index=2000, sources=1000)  # index más nuevo que las fuentes
    assert _frontend_needs_build(fe) is False


def test_frontend_without_package_json_never_builds(tmp_path):
    fe = tmp_path / "frontend"
    fe.mkdir()
    assert _frontend_needs_build(fe) is False