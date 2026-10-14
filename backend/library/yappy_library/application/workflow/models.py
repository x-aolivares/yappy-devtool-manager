"""The states a resource can be in on the Workflow/Recursos screen.

Five states, and only three of them are stored. The tracker records how a *start*
went; whether the process is still there is read live from the OS, and the two
together give five. Keeping the derived ones derived is what stops "the tunnel
died" and "the tunnel never came up" from collapsing into the same answer.

``down`` is not a tracked state at all: nothing is registered for something that
is not running, so it is simply the absence of an entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATE_DOWN = "down"
STATE_STARTING = "starting"
STATE_UP = "up"
STATE_ERROR = "error"
STATE_DEAD = "dead"

ALL_STATES = (STATE_DOWN, STATE_STARTING, STATE_UP, STATE_ERROR, STATE_DEAD)


def state_of(entry: dict) -> str:
    """The state of a tracked process, from its record plus its liveness.

    Liveness wins. A process recorded as ``up`` that is no longer there reads as
    ``dead`` whether it fell over on its own or a failed start took it down, and
    a process recorded as ``starting`` that is gone never became ``up`` — which
    is the truth the user needs, and not the one a stale record would give.
    """
    if not entry.get("alive"):
        return STATE_DEAD
    return entry.get("state") or STATE_UP


def is_foreign(entry: dict) -> bool:
    """True when something other than the web UI started this process.

    Those get a dashed border and an *ajeno* badge on screen, because stopping
    them reaches further than the screen suggests: ``kill_ssm()`` works off the
    tracker, so it does not care who started what.
    """
    from yappy_library.adapters.process_tracker import OWNER_WEB

    return entry.get("owner") != OWNER_WEB


def is_live(state: str) -> bool:
    """Whether a resource in this state holds a local port.

    Used for the port conflict check: a port held by something that is up or
    coming up conflicts; one held by something that fell over does not.
    """
    return state in (STATE_UP, STATE_STARTING)


@dataclass(frozen=True)
class PortEntry:
    """One row of the tunnel catalog: a local port and what it is for."""

    port: int
    label: str

    @property
    def display(self) -> str:
        return self.label or str(self.port)


@dataclass(frozen=True)
class ResourceDef:
    """A resource this tool knows how to manage, independent of its state.

    Ports that depend on the environment are resolved by the caller rather than
    stored here, so a definition never carries a stale port.
    """

    key: str
    group: str
    name: str
    detail: str
    remote_port: int | None = None
    local_port: int | None = None
    browser_url: str | None = None
    #: Cluster whose alias resolves the remote host. Empty for resources that do
    #: not go through a cluster.
    cluster: str = ""
    #: Set for the few that forward somewhere other than a cluster host.
    remote_host: str | None = None
    #: Name of the readiness probe that decides whether it really came up.
    probe: str = "http"


@dataclass(frozen=True)
class ResourceRow:
    """A resource definition paired with what the tracker says about it."""

    key: str
    group: str
    name: str
    detail: str
    state: str = STATE_DOWN
    remote_port: int | None = None
    """Where the traffic goes. A local resource has none: it listens here."""
    local_port: int | None = None
    pid: int | None = None
    since: float | None = None
    foreign: bool = False
    browser_url: str | None = None
    log_file: str = ""
    #: The cluster already holding this local port, when it is not this row.
    conflict: str | None = None


@dataclass(frozen=True)
class PortRow:
    """A catalog entry paired with the tunnel running on it, if any."""

    cluster: str
    port: int
    label: str
    state: str = STATE_DOWN
    pid: int | None = None
    since: float | None = None
    foreign: bool = False
    browser_url: str | None = None
    log_file: str = ""
    conflict: str | None = None


@dataclass(frozen=True)
class WorkflowSnapshot:
    """Everything one poll of the screen needs, computed once on the server.

    Conflicts and ownership are resolved here rather than in the browser: they
    need every cluster's catalog and every tracked process, and a client would
    either get half of it or fetch it row by row.
    """

    env: str
    cluster: str
    remote_host: str
    generated_at: float
    resources: list[ResourceRow] = field(default_factory=list)
    ports: list[PortRow] = field(default_factory=list)
    #: Tracked tunnels that predate the port field, or that forward somewhere the
    #: catalog does not describe. Shown apart rather than guessed at.
    unattributed: list[dict] = field(default_factory=list)