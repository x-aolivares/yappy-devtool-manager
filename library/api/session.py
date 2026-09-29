from __future__ import annotations

import subprocess
import time

from ..base import BaseCommand, _aws_cmd
from ..config import Config
from ..db.tunnel import _generate_token, _write_local_env
from ..logger import info, success, warn, die
from ..ssm.tunnel import CLUSTER_ALIASES
from .models import DBResult, PFResult


class ForwarderNamespace:
    def __init__(self, session: Session, target_type: str):
        self._session = session
        self._target_type = target_type

    def pf(self, ports: list[int], load_balance: str | None = None) -> PFResult:
        ports_list = [int(p) for p in ports]
        cfg = self._session._cfg
        cmd = self._session._cmd

        if self._target_type == "cluster":
            if not load_balance:
                die("load_balance is required for cluster port-forwarding")
            cluster_host = CLUSTER_ALIASES.get(load_balance)
            if not cluster_host:
                die(f"Unknown cluster '{load_balance}'. Use: cap, cap2, or int")
            domain = cfg.get("CLUSTER_DOMAIN", "yappycloud.com")
            remote_host = f"{cluster_host}.{cfg.env}.{domain}"
            target = f"cluster/{load_balance}"
        elif self._target_type == "bastion":
            remote_host = None
            target = "bastion"
        else:
            die(f"Unknown target type: {self._target_type}")

        for port in ports_list:
            if self._target_type == "bastion" and port == 9091:
                local_port = 3000
            else:
                local_port = port

            proc = cmd.ssm_tunnel(
                instance=cfg.require("AWS_INSTANCE"),
                port=port,
                local_port=local_port,
                region=cfg.require("AWS_REGION"),
                profile=cfg.profile,
                remote_host=remote_host,
                quiet=True,
            )
            self._session._procs.append(proc)
            info(f"Tunnel {target}:{port} -> localhost:{local_port}")

        return PFResult(ports=ports_list, target=target, load_balance=load_balance)


class Session:
    def __init__(self, env: str = "dev"):
        self._env = env
        self._cfg = Config.with_env(env)
        self._cmd = BaseCommand()
        self._procs: list[subprocess.Popen] = []

    @property
    def multiple(self) -> ForwarderNamespace:
        return ForwarderNamespace(self, "cluster")

    @property
    def bastion(self) -> ForwarderNamespace:
        return ForwarderNamespace(self, "bastion")

    def start(self) -> Session:
        self._cmd.validate_env(self._env)
        self._cmd.check_requirements("aws")

        result = self._cmd.run(
            [*_aws_cmd(), "sts", "get-caller-identity", "--profile", self._cfg.profile],
            capture=True,
            check=False,
        )
        if result.returncode != 0:
            warn("No active AWS session. Starting SSO login...")
            from ..aws.session import session as _aws_session
            _aws_session()
            result = self._cmd.run(
                [*_aws_cmd(), "sts", "get-caller-identity", "--profile", self._cfg.profile],
                capture=True,
                check=False,
            )
            if result.returncode != 0:
                die("AWS SSO login failed")
            success("AWS session established")
        else:
            success("AWS session active")
        return self

    def database(self) -> DBResult:
        cfg = self._cfg
        cmd = self._cmd

        token = _generate_token(cfg)
        _write_local_env(token)

        proc = cmd.ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=int(cfg.get("AWS_PORT", "53360")),
            local_port=cfg.db_port,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            remote_host=cfg.require("AWS_HOST"),
            quiet=True,
        )
        self._procs.append(proc)
        time.sleep(3)

        if proc.poll() is None:
            info(f"Database tunnel ready on localhost:{cfg.db_port}")
        else:
            warn(f"Database tunnel exited (code {proc.returncode})")

        return DBResult(port=cfg.db_port, password=token)

    def cleanup(self):
        for proc in self._procs:
            if proc.poll() is None:
                proc.terminate()
        self._procs.clear()
        self._cmd.kill_ssm()
