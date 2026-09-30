"""Caso de uso: estado y control del MySQL local."""
from __future__ import annotations

from ..domain.entities import DbConnectionInfo, LocalMysqlStatus
from ..domain.ports import DbProbe, LocalMysqlService


def local_status(service: LocalMysqlService) -> LocalMysqlStatus:
    return service.status()


def local_start(service: LocalMysqlService) -> LocalMysqlStatus:
    """Start the server and confirm the credentials actually work."""
    service.start()
    return service.verify_login()


def local_stop(service: LocalMysqlService) -> LocalMysqlStatus:
    return service.stop()


def probe_environment(probe: DbProbe, env: str) -> DbConnectionInfo:
    """Check whether an environment's tunnel is up. Never raises for a closed
    port — that is a state the UI renders, not an error."""
    return probe.probe(env)
