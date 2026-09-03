import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import yappy_cli.aws.session as aws_session
import yappy_cli.db.tunnel as tunnel
import yappy_cli.workflow.executor as executor_mod


def test_executor_never_prints_db_password(monkeypatch, capsys):
    class FakeDB:
        port = 8100
        password = "SUPERSECRET"

    class FakeMultiple:
        def pf(self, ports, load_balance=None):
            return object()

    class FakeBastion:
        def pf(self, ports):
            return object()

    class FakeSession:
        multiple = FakeMultiple()
        bastion = FakeBastion()

        def start(self):
            return self

        def database(self):
            return FakeDB()

    class FakeKafka:
        def up(self, target):
            pass

    class FakeDevUtils:
        def kafka(self):
            return FakeKafka()

    monkeypatch.setattr(executor_mod, "Session", lambda env: FakeSession())
    monkeypatch.setattr(executor_mod, "DevUtils", lambda: FakeDevUtils())

    executor_mod.executor("dev")

    out = capsys.readouterr().out
    assert "DB password" not in out
    assert "SUPERSECRET" not in out


def test_mfa_without_code_prompts_and_never_leaks_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(aws_session, "_aws_cmd", lambda: ["aws"])
    monkeypatch.setattr(aws_session.aws_cmd, "check_requirements", lambda *a: None)

    prompts = []
    monkeypatch.setattr(aws_session.getpass, "getpass", lambda prompt: prompts.append(prompt) or "123456")

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        joined = " ".join(str(c) for c in cmd)
        if "get-caller-identity" in joined:
            return SimpleNamespace(returncode=0, stdout="123456789012", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"Credentials": {
                "AccessKeyId": "AKIAEXAMPLE",
                "SecretAccessKey": "SUPERSECRET",
                "SessionToken": "STOKEN",
            }}),
            stderr="",
        )

    monkeypatch.setattr(aws_session.subprocess, "run", fake_run)

    aws_session.mfa(user="alice", token=None, quiet_deprecation=True)

    assert prompts == ["MFA code: "]
    assert len(calls) == 2, "credentials must be written directly, not via `aws configure set`"
    for cmd in calls:
        assert "SUPERSECRET" not in cmd
        assert "aws_secret_access_key" not in cmd

    creds = (tmp_path / ".aws" / "credentials").read_text()
    assert "[base-profile]" in creds
    assert "aws_secret_access_key = SUPERSECRET" in creds
    cfg = (tmp_path / ".aws" / "config").read_text()
    assert "[profile base-profile]" in cfg


def test_mfa_credentials_helper_preserves_other_profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    aws_dir = tmp_path / ".aws"
    aws_dir.mkdir(parents=True, exist_ok=True)
    (aws_dir / "credentials").write_text("[other]\naws_access_key_id = OTHERKEY\n")
    (aws_dir / "config").write_text("[profile other]\nregion = us-west-2\n")

    aws_session._write_mfa_credentials(
        ["base-profile", "mfa"],
        {"AccessKeyId": "AKIAEXAMPLE", "SecretAccessKey": "SECRETVAL", "SessionToken": "TOKENVAL"},
    )

    creds = (aws_dir / "credentials").read_text()
    assert "OTHERKEY" in creds
    assert "[base-profile]" in creds
    assert "[mfa]" in creds
    assert "aws_secret_access_key = SECRETVAL" in creds

    cfg = (aws_dir / "config").read_text()
    assert "[profile other]" in cfg
    assert "region = us-west-2" in cfg
    assert "[profile base-profile]" in cfg


def test_write_local_env_sets_0600_on_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(tunnel.os, "name", "posix")
    chmods = []
    monkeypatch.setattr(tunnel.os, "chmod", lambda path, mode: chmods.append((Path(path), mode)))
    monkeypatch.setattr(tunnel, "_clipboard", lambda text: None)

    target = tmp_path / ".env.local"
    tunnel._write_local_env("SECRETTOKEN", env_local=target)

    def _fs(p):
        return str(p).replace("\\", "/")

    assert [(_fs(p), m) for p, m in chmods] == [(_fs(target), 0o600)]
    assert "DB_PASSWORD=SECRETTOKEN" in target.read_text()
