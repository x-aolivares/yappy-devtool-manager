"""Starting and stopping, as one operation each.

Every start goes through the same three steps (see :mod:`.supervisor`): spawn,
record ``starting``, supervise. Every stop goes through :func:`.stop_entry`, so
the refusal to stop somebody else's process lives in one place instead of being
re-derived per caller.

Two rules the screen depends on:

- **A start that loses a port race does not become an error row.** It returns a
  refusal. Turning "somebody else holds 8403" into a red card on *this* row
  would point the developer at the wrong thing.
- **Stopping a foreign process is refused, not performed.** ``kill_ssm()`` works
  off the tracker and does not care who started what, so it would happily close
  a tunnel another project opened. The screen shows those with a dashed border
  for exactly this reason, and this is the check that backs it up.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from yappy_library.adapters import process_tracker
from yappy_library.adapters.logging import info as log_info
from yappy_library.adapters.processes import BaseCommand, ToolUnavailable, tunnel_log_path
from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES, cluster_domain
from yappy_library.application.workflow.models import is_foreign, is_live, state_of
from yappy_library.application.workflow.registry import resources_for
from yappy_library.application.workflow.service import _owner
from yappy_library.application.workflow.supervisor import stop_pid, supervise
from yappy_library.config import Config, aws_port_of


class StartRefused(Exception):
    """A start that will not happen, and why.

    A refusal is an outcome with a reason the screen can show ("otro cluster ya
    tiene el 8403"), not a failure to swallow.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class StopRefused(Exception):
    """A stop that will not happen, and why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _alive_holders() -> dict[int, list[dict]]:
    """Every live tracked process, grouped by the local port it holds."""
    holders: dict[int, list[dict]] = {}
    for entry in process_tracker.get_tracked_processes():
        port = entry.get("local_port")
        if port is None or not entry.get("alive"):
            continue
        holders.setdefault(port, []).append(entry)
    return holders


def _describe(entry: dict) -> str:
    cluster = entry.get("cluster")
    if cluster:
        return f"el cluster {cluster}"
    return f"el recurso {entry.get('target') or entry.get('pid')}"


def ensure_port_free(port: int, cluster: str = "") -> None:
    """Forget the ghosts on ``port``, then refuse if somebody live holds it.
    ``cluster`` is the cluster of the row trying to start. The test is the same
    one the snapshot uses — ownership is ``(cluster, port)``, not ``port`` — so
    "this row is already up" and "another cluster has it" stay distinguishable
    here too.

    The pruning comes first because the check below only sees live entries: a
    process that fell over leaves its entry behind, the port reads as free, and
    each start stacks another ghost on the same port.
    """
    process_tracker.prune_dead_on_port(port)
    holders = _alive_holders().get(port, [])
    if _owner(holders, cluster) is not None:
        return
    for entry in holders:
        if is_live(state_of(entry)):
            raise StartRefused(f"El puerto local {port} ya lo está usando {_describe(entry)}")


def _definition(cfg: Config, key: str):
    for definition in resources_for(cfg):
        if definition.key == key:
            return definition
    if not getattr(cfg, "env", None):
        # El ámbito sin ambiente —la pantalla de Kafka—: no hay AWS, y por lo
        # tanto no hay nada que abrir más que el broker local.
        raise StartRefused(
            f"Sin ambiente no hay el recurso '{key}'. "
            "Sólo Kafka corre sin AWS."
        )
    if getattr(cfg, "is_local", False):
        raise StartRefused(
            f"El ambiente {cfg.env or ''} es local: no tiene el recurso '{key}'. "
            "Sólo Kafka corre sin AWS."
        )
    raise StartRefused(f"No conozco el recurso '{key}'")


def _spawn_tunnel(
    cfg: Config,
    *,
    port: int,
    local_port: int,
    remote_host: str | None,
    cluster: str,
    label: str,
    env: str,
) -> subprocess.Popen:
    """One ``ssm start-session``, tracked with everything the screen needs.

    **Why the ``ToolUnavailable`` arm is here and not in the routes.** This is
    the one place all three start paths converge — ``start_resource``,
    ``start_port`` and ``_start_db`` all funnel through it — so the conversion
    lives here once instead of being re-derived per endpoint, and a new caller
    inherits it for free. A missing AWS CLI used to raise ``SystemExit`` out of
    ``_aws_cmd`` straight through this function into a Starlette worker thread,
    where it killed uvicorn with no traceback. A refusal is the shape the screen
    already knows: the route turns it into a 409 and the row renders the reason.

    The label keeps the message honest about *what* failed, because the reason
    ("AWS CLI not found…") is identical no matter which row was clicked.
    """
    try:
        return BaseCommand().ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=port,
            local_port=local_port,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            remote_host=remote_host,
            quiet=True,
            log_file=tunnel_log_path(local_port),
            env=env,
            cluster=cluster,
            label=label,
            owner=process_tracker.OWNER_WEB,
        )
    except ToolUnavailable as exc:
        raise StartRefused(
            f"No se pudo levantar {label or 'el túnel'}: {exc.reason}"
        ) from exc
    except ValueError as exc:
        # `cfg.require` raises ValueError for a missing AWS_INSTANCE/AWS_REGION.
        # It used to escape into the worker thread as a 500 with a traceback;
        # the row knows how to show a refusal, so let it.
        raise StartRefused(
            f"No se pudo levantar {label or 'el túnel'}: {exc}"
        ) from exc


def _remote_host_for(cfg: Config, definition, cluster: str) -> str | None:
    """Where a tunnel's packets go: the definition's own host, or the cluster's."""
    if definition is not None and definition.remote_host:
        return definition.remote_host
    host = CLUSTER_ALIASES.get(cluster)
    if not host:
        return None
    return f"{host}.{cfg.env}.{cluster_domain(cfg)}"


def start_resource(cfg: Config, key: str) -> dict:
    """Start one resource by registry key and hand it to a supervisor."""
    definition = _definition(cfg, key)
    port = definition.local_port
    if not port:
        raise StartRefused(f"{definition.name} no tiene puerto local")

    ensure_port_free(port)

    if definition.group == "kafka":
        proc = _start_kafka(definition)
    elif definition.group == "db":
        proc = _start_db(cfg, port)
    else:
        proc = _spawn_tunnel(
            cfg,
            port=definition.remote_port or port,
            local_port=port,
            remote_host=_remote_host_for(cfg, definition, definition.cluster),
            cluster=definition.cluster,
            label=definition.name,
            env=cfg.env or "",
        )

    return _hand_off(proc, port, definition.probe, definition.name)


def _start_kafka(definition) -> subprocess.Popen:
    """Start a local Kafka process through the service, not through the CLI.

    ``yappy_cli.kafka.manager.up`` is a Typer command: it prints, it can call
    ``die()``, and it lives in another package from the one that needs it.

    ``KafkaUnavailable`` becomes a refusal rather than propagating, because the
    reason it exists is this function: the service used to end the process on a
    missing ``libs/`` or ``main.jar``, and the process it ended was uvicorn. A
    refusal is the shape the screen already knows — it becomes a 409 with the
    message in ``routes/workflow.py`` — and the message stays in English inside
    so the terminal keeps printing exactly what it printed before.
    """
    from yappy_library.adapters.kafka.service import KafkaService, KafkaUnavailable

    target = "server" if definition.key == "kafka-server" else "ui"
    try:
        proc = KafkaService(Config()).up(target, detach=True, owner=process_tracker.OWNER_WEB)
    except KafkaUnavailable as exc:
        raise StartRefused(f"No se pudo levantar {definition.name}: {exc.reason}") from exc
    if proc is None:
        raise StartRefused("Kafka no devolvió un proceso")
    return proc


def _start_db(cfg: Config, port: int) -> subprocess.Popen:
    """Open the Aurora tunnel: token first, then the forward.

    Not the CLI's ``run db``: that one also wires the token refresher and the
    keep-alive loop, which are a terminal session's job. The screen opens the
    forward and supervises it, and the RDS token it writes is the same file the
    other pages read.
    """
    from yappy_library.adapters.database.credentials import generate_token, write_local_env

    try:
        write_local_env(generate_token(cfg))
        remote_host = cfg.require("AWS_HOST")
    except SystemExit as exc:
        raise StartRefused(f"No se pudo generar el token de RDS: {exc}") from exc
    except ValueError as exc:
        # A missing AWS_HOST is a configuration refusal, not a server crash.
        # The route already knows how to show a `StartRefused` (409 + reason).
        raise StartRefused(f"No se pudo generar el token de RDS: {exc}") from exc

    return _spawn_tunnel(
        cfg,
        port=aws_port_of(cfg),
        local_port=port,
        remote_host=remote_host,
        cluster="",
        label="Tunnel Aurora",
        env=cfg.env or "",
    )


def start_port(cfg: Config, cluster: str, port: int, label: str = "") -> dict:
    """Start a catalog row: a tunnel to ``port`` on ``cluster``.

    **Sin ambiente no hay túnel.** Un túnel se abre contra un ambiente, y el
    ámbito vacío es el de la pantalla de Kafka —que es full local—. Esta
    refusal es la que contesta a una pestaña vieja o a un `curl` que no mandó
    un ambiente.
    """
    if not getattr(cfg, "env", None):
        raise StartRefused(
            "Falta el ambiente: un túnel de cluster se abre contra un ambiente, "
            "no contra la máquina local."
        )
    if getattr(cfg, "is_local", False):
        # The catalog is hidden for local environments, but the endpoint is
        # reachable by a stale tab or a curl. Refusing here is what keeps "local
        # means no AWS" true on every path instead of only on the happy one.
        raise StartRefused(
            f"El ambiente {cfg.env or ''} es local: se conecta por TCP y no abre "
            "túneles SSM."
        )
    ensure_port_free(port, cluster=cluster)

    remote_host = _remote_host_for(cfg, None, cluster)
    if remote_host is None:
        raise StartRefused(f"'{cluster}' no es un cluster conocido")

    proc = _spawn_tunnel(
        cfg,
        port=port,
        local_port=port,
        remote_host=remote_host,
        cluster=cluster,
        label=label,
        env=cfg.env or "",
    )
    return _hand_off(proc, port, "https", label or f":{port}")




def _hand_off(proc: subprocess.Popen, port: int, probe: str, label: str) -> dict:
    """Record ``starting``, then supervise without waiting.

    The state is written before the supervisor exists so a snapshot taken in the
    gap reads "coming up" rather than "down" — and "down" would invite a second
    click on a port that is already being opened.

    ``supervisor`` is this process. If the server shuts down before the probe
    finishes, the entry says so and the row reads `dead` instead of waiting
    forever for a verdict that will never come.

    The pid/port/probe/supervisor chatter that used to sit here was diagnostic
    scaffolding: it existed because a start was failing and the log said
    nothing about how far it got. Once the failures were fixed it was pure
    noise — six lines per start describing plumbing that either worked or
    raised. What the supervisor writes when it has a **verdict** is the part
    worth reading, and that is still there.
    """
    process_tracker.track_process_state_and_supervisor(
        proc.pid, process_tracker.STATE_STARTING, os.getpid()
    )
    supervise(proc.pid, port, probe, label=label, log_file=_log_file_for(proc.pid))
    return {"pid": proc.pid, "port": port, "state": process_tracker.STATE_STARTING, "label": label}


def _log_file_for(pid: int) -> str:
    """Where the supervised process writes, read back from the tracker.

    Read back rather than reconstructed: the tunnel log is
    ``~/.yappy/logs/tunnel-<port>.log`` and the Kafka log is
    ``<install>/temp-logs/kafka-<target>-<pid>.log``. A supervisor that guesses
    the first shape for every resource sends the reader to a file that is not
    there — which is exactly what happened with Kafka.
    """
    for entry in process_tracker.get_tracked_processes():
        if entry.get("pid") == pid:
            return entry.get("log_file") or ""
    return ""


def _entry_for(pid: int) -> dict | None:
    for entry in process_tracker.get_tracked_processes():
        if entry.get("pid") == pid:
            return entry
    return None


def stop_entry(pid: int, *, allow_foreign: bool = False) -> dict:
    """Stop one tracked process.

    ``allow_foreign`` exists for the screen's explicit "Parar todo", where the
    developer has been warned that the reach is wider than the screen. It is not
    the default, because the default is the button on a row, and a row button
    should never reach a process the screen drew with a dashed border.
    """
    entry = _entry_for(pid)
    if entry is None:
        raise StopRefused(f"El proceso {pid} no está trackeado")

    if is_foreign(entry) and not allow_foreign:
        raise StopRefused(
            f"El proceso {pid} no lo levantó la web. Paralo desde la terminal "
            f"si querés cerrarlo."
        )

    return {"pid": pid, "stopped": stop_pid(pid)}


def stop_all(*, allow_foreign: bool = False) -> dict:
    """Stop everything alive, and report what was left alone."""
    stopped: list[int] = []
    refused: list[dict] = []

    motivos: list[str] = []
    parados: list[tuple[dict, int]] = []

    for entry in process_tracker.get_tracked_processes():
        if not entry.get("alive"):
            continue
        pid = entry.get("pid") or 0
        if is_foreign(entry) and not allow_foreign:
            refused.append({"pid": pid, "reason": _describe(entry)})
            motivos.append(f"{pid}: no lo levantó la web")
            continue
        if stop_pid(pid):
            stopped.append(pid)
            parados.append((entry, pid))
        else:
            # The kill was refused, not fumbled: the process is still running.
            # Listing it under `stopped` is the one answer the screen must never
            # give, because it is what makes the developer press the button again
            # and wonder why the same thing keeps coming back.
            refused.append({"pid": pid, "reason": _describe(entry)})
            motivos.append(
                f"{pid}: el guard se negó — el motivo exacto está en el ERR de arriba"
            )

    # Two different refusals used to print the same sentence, so a guard refusal
    # read as "no lo levantó la web" when the truth was "that pid is an ancestor
    # of this server". A log that names the wrong cause sends the reader looking
    # for a process ownership problem that does not exist.
    for motivo in motivos:
        log_info(f"No se paró {motivo}")

    # The successes used to say nothing at all. A "Parar todo" that works left
    # the terminal with a `POST …/stop-all 200 OK` from uvicorn and not one word
    # about what it had just closed — so "did that do anything?" was unanswerable
    # from the log. Silence on the happy path is its own kind of untruth.
    #
    # The tracker entry is read *before* the untrack, so the description names
    # the row the developer clicked rather than a bare pid.
    for entry, pid in parados:
        log_info(f"Se paró {pid} ({_describe(entry)})")

    return {"stopped": stopped, "refused": refused}