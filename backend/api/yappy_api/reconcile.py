"""Startup reconciliation of the process tracker.

``~/.yappy/tracker/<pid>.json`` is a filesystem registry that outlives the
process it names. Nothing removes an entry when a process dies on its own, and
nothing removes a CLI-started entry at all, so the directory accumulates claims
about pids that stopped existing — or, worse, that Windows has since handed to
somebody else. ``_process_alive`` answers "is there a process with this pid",
which on Windows is not the same question as "is this still the tunnel we
started".

**The symptom that forced this.** A ``Kafka Server`` row wearing the *ajeno*
badge with a live-looking pid, after the developer had killed everything. The web
cannot produce a foreign kafka entry — every ``track_process`` call site passes
``owner`` explicitly and ``actions._start_kafka`` passes ``OWNER_WEB`` — so the
row was displaying an entry the web did not write, naming a pid now owned by a
stranger.

**Why this is allowed to kill.** Only because the guards in
``BaseCommand._kill_pid`` are in place: ``taskkill /t`` on a recycled pid takes
down whatever tree that pid is in now, and on a pid that is an ancestor of the
server it takes down the server. Both are fixed there (identity gates +
unconditional ancestor guard). That is why this cleanup was unaffordable before
and exists now.

**Shape.** Policy over a list, not a loop with side effects. :func:`plan` is
pure — it takes the entries plus two liveness predicates and returns decisions —
and :func:`reconcile` is the thin caller that executes them, logs one line each
and closes with a one-line summary. That split is what makes the rules testable
without a tracker, without the OS and without a process tree.

**Ordering, and the one rule that needed reading twice.** Rule 2 ("leave
``owner=cli`` alone") protects *live* processes: the web must never stop
something a developer started in their own terminal. A dead one is still a
ghost, and forgetting a dead process touches nothing at all — so it falls through
to rule 3 and disappears. Reading rule 2 as "leave every cli entry alone
forever" would preserve exactly the garbage this module exists to remove.

Fail-safe everywhere else: anything uncertain is logged and the process is left
running.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable

from yappy_library.adapters import process_tracker
from yappy_library.adapters.logging import info, warn
from yappy_library.adapters.processes import BaseCommand
from yappy_library.adapters.process_tracker import OWNER_WEB

#: What to do with one entry. ``KILL`` implies ``UNTRACK``: the caller stops the
#: process and then forgets it, because an entry whose process is gone can only
#: ever answer ``ensure_port_free`` with a pid that no longer means anything.
KEEP = "keep"
UNTRACK = "untrack"
KILL = "kill"

#: Why. Data rather than prose: the summary counts by it and the tests assert on
#: it, so a rule change that only moves a label is still visible.
REASON_LIVE = "live"
REASON_FOREIGN = "foreign"
REASON_ORPHAN = "orphan"
REASON_GHOST = "ghost"

#: What :func:`_execute` reports back, one per decision.
DONE_KILLED = "killed"
DONE_REFUSED = "refused"
DONE_FORGOTTEN = "forgotten"
DONE_KEPT = "kept"


@dataclass(frozen=True)
class Decision:
    """One entry, and what reconciliation decided to do with it."""

    action: str
    pid: int
    reason: str
    #: Short human name for the log line — the row's label, else target, else
    #: resource, else the bare pid. Computed in ``plan`` so the executor only has
    #: to print, never to re-derive.
    label: str


def _label(entry: dict) -> str:
    what = (
        entry.get("label")
        or entry.get("target")
        or entry.get("resource")
        or entry.get("pid")
    )
    port = entry.get("local_port")
    return f"{what}:{port}" if port else str(what)


def _decide(entry: dict, alive: Callable, supervisor_alive: Callable) -> tuple[str, str]:
    """``(action, reason)`` for one entry. The rules are in the module docstring."""
    pid = entry.get("pid")
    web_owned = entry.get("owner") == OWNER_WEB
    supervisor = entry.get("supervisor")

    # Rule 1 — the entry names the web server that was going to resolve it, and
    # that server is gone. Nobody will ever supervise this process again, nobody
    # asked for it, and it holds a local port the next start needs. A live one is
    # an orphan; a dead one is a pure ghost. ``owner`` is part of the test on
    # purpose: the supervisor field belongs to the web alone, and a web that kills
    # a process it does not own would do to the terminal exactly what the *ajeno*
    # badge promises it will not.
    if web_owned and supervisor and not supervisor_alive(supervisor):
        if alive(pid):
            return KILL, REASON_ORPHAN
        return UNTRACK, REASON_GHOST

    # Rule 2 — the terminal started this one. Left running, and logged as seen,
    # so "the web touched my tunnel" can be ruled out from the log alone. A dead
    # one gets no protection from this rule: there is nothing running to protect.
    if not web_owned and alive(pid):
        return KEEP, REASON_FOREIGN

    # Rule 3 — nothing is running under this pid, or it is a recycled pid the
    # identity gates would refuse. Either way the entry can only mislead:
    # ``ensure_port_free`` looks at liveness alone, so it keeps being read as a
    # live claim on a port for as long as the file exists.
    if not alive(pid):
        return UNTRACK, REASON_GHOST

    # Rule 4 — a web-owned process whose web server is still up. Touching it
    # would kill a tunnel the running server is actively supervising.
    return KEEP, REASON_LIVE


def plan(
    entries: list[dict],
    alive: Callable[[int], bool],
    supervisor_alive: Callable[[int], bool],
) -> list[Decision]:
    """Decide what to do with every tracked entry, without doing any of it.

    ``alive`` answers "is the tracked process still there"; ``supervisor_alive``
    answers "is the web server that was going to resolve it still there". Both
    are injected rather than read from the OS so the rules can be exercised
    against pids nobody spawned.
    """
    decisions: list[Decision] = []
    for entry in entries:
        action, reason = _decide(entry, alive, supervisor_alive)
        decisions.append(
            Decision(
                action=action,
                pid=entry.get("pid"),
                reason=reason,
                label=_label(entry),
            )
        )
    return decisions


def _predicates(entries: list[dict]) -> tuple[Callable[[int], bool], Callable[[int], bool]]:
    """The two liveness answers, read from the snapshot we already paid for.

    ``get_tracked_processes`` already asked the OS about every tracked pid and
    every supervisor, and on Windows each of those answers is a full toolhelp
    snapshot. Asking again would double the cost — and, worse, could contradict
    itself: a pid recycled between two reads gives two different answers, and
    the only place that matters is a decision to kill something.
    """
    live = {entry.get("pid"): bool(entry.get("alive")) for entry in entries}
    supervisors = {
        entry["supervisor"]: bool(entry.get("supervisor_alive", True))
        for entry in entries
        if entry.get("supervisor")
    }
    return (
        lambda pid: live.get(pid, False),
        lambda pid: supervisors.get(pid, False),
    )


def _execute(decision: Decision) -> str:
    """Carry out exactly one decision, and say what happened to it."""
    if decision.action == KILL:
        stopped = BaseCommand().kill_ssm(decision.pid)
        process_tracker.untrack_process(decision.pid)
        if stopped:
            info(
                f"reconciliación: {decision.label} (pid {decision.pid}) era "
                f"huérfano de una sesión web muerta; parado y olvidado"
            )
            return DONE_KILLED
        # `kill_ssm` answers False when one of the three gates in `_kill_pid`
        # refused — the pid is not provably the one we started, or it contains
        # this very server. The process was left running, which is the guard
        # doing its job. The entry still names a pid this tool can no longer
        # claim, so it goes anyway: same stance as rule 3, and the row it was
        # drawing was a stranger wearing our identity fields.
        warn(
            f"reconciliación: {decision.label} (pid {decision.pid}) es huérfano, "
            f"pero el guard se negó a pararlo (el motivo exacto está en el ERR de "
            f"arriba). Se olvida la entrada y el proceso sigue vivo"
        )
        return DONE_REFUSED

    if decision.action == UNTRACK:
        process_tracker.untrack_process(decision.pid)
        info(
            f"reconciliación: {decision.label} (pid {decision.pid}) ya no es el "
            f"proceso que se registró; fantasma olvidado"
        )
        return DONE_FORGOTTEN

    if decision.reason == REASON_FOREIGN:
        info(
            f"reconciliación: {decision.label} (pid {decision.pid}) lo levantó la "
            f"terminal, no la web; se deja como está"
        )
    else:
        info(
            f"reconciliación: {decision.label} (pid {decision.pid}) sigue vivo y con "
            f"su sesión web viva; se deja como está"
        )
    return DONE_KEPT


def reconcile(
    *,
    alive: Callable[[int], bool] | None = None,
    supervisor_alive: Callable[[int], bool] | None = None,
) -> dict:
    """Reconcile the whole tracker once, and return what was done with it.

    One decision per entry, one log line per decision, one summary line at the
    end. The summary is the whole point of logging this: it is what makes a
    surprising row on screen answerable later ("it was already there at startup
    and the web said it left it alone").

    The optional predicates exist for tests. A failure to carry out one decision
    is logged and counted rather than raised, so one unreadable file cannot
    abandon the reconciliation of everything else.
    """
    entries = process_tracker.get_tracked_processes()
    default_alive, default_supervisor = _predicates(entries)
    decisions = plan(entries, alive or default_alive, supervisor_alive or default_supervisor)

    outcomes: Counter = Counter()
    for decision in decisions:
        try:
            outcomes[_execute(decision)] += 1
        except Exception as exc:  # noqa: BLE001 - a cleanup must not abandon itself
            outcomes["failed"] += 1
            warn(
                f"reconciliación: no se pudo decidir por {decision.label} "
                f"(pid {decision.pid}): {exc}. Se deja el proceso como está"
            )

    kept = sum(1 for d in decisions if d.reason == REASON_LIVE)
    foreign = sum(1 for d in decisions if d.reason == REASON_FOREIGN)

    # Nothing to audit, nothing said: this line is paid on every `yappy web` and
    # a tracker with nothing in it is the normal case, not news.
    #
    # The buckets are disjoint and they add up to `len(decisions)`. They used to
    # overlap — `intactos` counted the foreign ones too, and the summary
    # reported 4 decisions for 3 entries — which made the line useless for the
    # only thing it is for: checking it against the entries above it.
    if decisions:
        parts = [
            f"{kept} intactos",
            f"{outcomes[DONE_KILLED]} huérfanos parados",
            f"{outcomes[DONE_FORGOTTEN]} fantasmas olvidados",
            f"{foreign} ajenos sin tocar",
        ]
        if outcomes[DONE_REFUSED]:
            parts.append(f"{outcomes[DONE_REFUSED]} huérfanos que el guard no quiso parar")
        if outcomes["failed"]:
            parts.append(f"{outcomes['failed']} sin poder decidir")
        info(f"reconciliación del tracker: {', '.join(parts)}")

    return {
        "decisions": decisions,
        "kept": kept,
        "killed": outcomes[DONE_KILLED],
        "ghosts": outcomes[DONE_FORGOTTEN],
        "foreign": foreign,
        "refused": outcomes[DONE_REFUSED],
        "failed": outcomes["failed"],
    }