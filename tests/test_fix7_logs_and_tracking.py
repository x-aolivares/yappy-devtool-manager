from pathlib import Path
from types import SimpleNamespace

import pytest

import yappy_cli.api.kafka as api_kafka
import yappy_cli.base as base
import yappy_cli.process_tracker as process_tracker
import yappy_cli.verbs.logs as logs_mod
from yappy_cli.verbs.logs import tail_log


def test_tail_log_oneshot_prints_last_n_lines(tmp_path, capsys):
    log_file = tmp_path / "app.log"
    log_file.write_text("\n".join(f"line{i}" for i in range(10)) + "\n")

    tail_log(log_file, follow=False, lines=3)

    out = capsys.readouterr().out
    assert "line7" in out
    assert "line9" in out
    assert "line0" not in out


def test_tail_log_follow_prints_appended_line_between_polls(monkeypatch, tmp_path, capsys):
    log_file = tmp_path / "app.log"
    log_file.write_text("a\nb\nc\n")

    appended = {"n": 0}

    def fake_sleep(seconds):
        appended["n"] += 1
        if appended["n"] == 1:
            log_file.write_text(log_file.read_text() + "d\n")
        else:
            raise KeyboardInterrupt()

    monkeypatch.setattr(logs_mod.time, "sleep", fake_sleep)

    tail_log(log_file, follow=True, lines=2)

    out = capsys.readouterr().out
    assert "b" in out
    assert "c" in out
    assert "d" in out
    assert "a" not in out


def test_show_logs_follow_prints_ctrl_c_hint(monkeypatch, tmp_path, capsys):
    log_file = tmp_path / "svc.log"
    log_file.write_text("hello\n")
    monkeypatch.setattr(
        process_tracker,
        "get_tracked_processes",
        lambda resource=None, target=None: [{
            "pid": 99, "resource": "kafka", "target": "server",
            "alive": True, "log_file": str(log_file),
        }],
    )
    monkeypatch.setattr(
        logs_mod.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    logs_mod._show_logs("kafka", "server", follow=True, lines=50)

    out = capsys.readouterr().out
    assert "Press Ctrl+C to stop following" in out
    assert "hello" in out


def test_kafka_service_detach_tracks_process_with_log_file(monkeypatch, tmp_path):
    tracked = []
    monkeypatch.setattr(api_kafka.process_tracker, "track_process", lambda **kw: tracked.append(kw))
    fake_proc = SimpleNamespace(pid=4321)
    monkeypatch.setattr(api_kafka.subprocess, "Popen", lambda cmd, **kw: fake_proc)
    monkeypatch.setattr(api_kafka.time, "sleep", lambda seconds: None)

    core = tmp_path / "kafka-core"
    (core / "libs").mkdir(parents=True)
    (core / "config" / "kraft").mkdir(parents=True)
    (core / "config" / "kraft" / "server.properties").write_text("")

    cfg = SimpleNamespace(
        kafka_core_path=str(core),
        kafka_ui_path=str(tmp_path / "kafka-ui"),
        kafka_path=str(tmp_path / "kafka"),
    )
    svc = api_kafka.KafkaService(cfg)
    proc = svc.up("server", detach=True)

    assert proc.pid == 4321
    assert tracked, "process must be tracked when started detached"
    entry = tracked[0]
    assert entry["resource"] == "kafka"
    assert entry["target"] == "server"
    assert "kafka-server-4321.log" in entry["log_file"]
    assert (tmp_path / "kafka" / "temp-logs" / "kafka-server-4321.log").exists()


def test_kafka_service_down_untracks_process(monkeypatch, tmp_path):
    untracked = []
    monkeypatch.setattr(process_tracker, "untrack_process", lambda pid: untracked.append(pid))
    monkeypatch.setattr(
        process_tracker,
        "get_tracked_processes",
        lambda resource=None, target=None: [{"pid": 4321}],
    )
    monkeypatch.setattr(
        api_kafka.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=""),
    )

    svc = api_kafka.KafkaService(SimpleNamespace(
        kafka_core_path=str(tmp_path / "kafka-core"),
        kafka_ui_path=str(tmp_path / "kafka-ui"),
        kafka_path=str(tmp_path / "kafka"),
    ))
    svc.down("server")

    assert untracked == [4321]


def test_serve_detach_creates_log_file_and_tracks(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    tracked = []
    monkeypatch.setattr(base.process_tracker, "track_process", lambda **kw: tracked.append(kw))
    monkeypatch.setattr(base.process_tracker, "get_tracked_processes", lambda **kw: [])
    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)

    proc = SimpleNamespace(pid=777, wait=lambda: None, terminate=lambda: None)
    base.BaseCommand().serve(proc, detach=True, name="DB tunnel", local_port=8100)

    log_file = tmp_path / ".yappy" / "logs" / "db-tunnel.log"
    assert log_file.exists()
    assert tracked == [{
        "pid": 777, "resource": "service", "target": "DB tunnel",
        "log_file": str(log_file),
    }]
    out = capsys.readouterr().out
    assert "PID 777" in out


def test_serve_detach_keeps_existing_tracking(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    tracked = []
    monkeypatch.setattr(base.process_tracker, "track_process", lambda **kw: tracked.append(kw))
    monkeypatch.setattr(
        base.process_tracker,
        "get_tracked_processes",
        lambda **kw: [{"pid": 777, "resource": "tunnel", "target": "dev"}],
    )
    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)

    proc = SimpleNamespace(pid=777, wait=lambda: None, terminate=lambda: None)
    base.BaseCommand().serve(proc, detach=True, name="DB tunnel")

    assert tracked == [], "an already-tracked pid must not be re-tracked"
