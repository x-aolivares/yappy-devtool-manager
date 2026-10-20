from __future__ import annotations

import subprocess

from yappy_library.adapters.aws_sso import login as sso_login
from yappy_library.adapters.database.connection import SyncError, _wait_for_port
from yappy_library.adapters.database.credentials import generate_token, write_local_env
from yappy_library.adapters.logging import die, info, success, warn
from yappy_library.adapters.processes import BaseCommand, _aws_cmd
from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES
from yappy_library.config import Config
from yappy_library.domain.models import DBResult, PFResult

#: How long to wait for MySQL to answer on the freshly opened tunnel. The same
#: bound `connect()` uses for an on-demand tunnel.
DB_TUNNEL_READY_TIMEOUT = 30.0


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
                env=cfg.env or "",
                cluster=load_balance or "",
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
            # El login vive en la library y no en el CLI: esta línea antes
            # importaba `..aws.session`, que desde `yappy_library/application`
            # apunta a `yappy_library.aws` —un paquete que no existe— así que
            # el camino que se toma justamente cuando falta la sesión moría con
            # `ModuleNotFoundError` en vez de abrir el navegador.
            sso_login(self._cfg.profile)
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

        token = generate_token(cfg)
        write_local_env(token)

        proc = cmd.ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=int(cfg.get("AWS_PORT", "53360")),
            local_port=cfg.db_port,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            remote_host=cfg.require("AWS_HOST"),
            quiet=True,
            env=cfg.env or "",
            label="db",
        )
        self._procs.append(proc)

        # A fixed sleep is not a readiness check. It answers a question nobody
        # asked — "did three seconds pass?" — and on a cold instance the answer
        # is no: the token lives fifteen minutes and the data channel needs a
        # moment longer than that. What the caller wants to know is whether the
        # tunnel carries bytes, which is what the greeting answers.
        try:
            _wait_for_port(cfg.db_port, timeout=DB_TUNNEL_READY_TIMEOUT)
            ready = True
        except SyncError:
            # The tunnel opened its port and MySQL never answered. That is not
            # fatal here — `database()` hands back a port and a token, and the
            # caller decides what to do. It is fatal to *pretend* it is ready.
            ready = False

        if proc.poll() is not None:
            warn(f"Database tunnel exited (code {proc.returncode})")
        elif not ready:
            warn(
                f"El túnel abrió el puerto {cfg.db_port} pero MySQL no respondió en "
                f"{DB_TUNNEL_READY_TIMEOUT:g}s. Revisá el log del túnel antes de correr "
                f"cualquier cosa contra la base."
            )
        else:
            info(f"Database tunnel ready on localhost:{cfg.db_port}")

        return DBResult(port=cfg.db_port, password=token)

    def cleanup(self):
        for proc in self._procs:
            if proc.poll() is None:
                proc.terminate()
        self._procs.clear()
        self._cmd.kill_ssm()
