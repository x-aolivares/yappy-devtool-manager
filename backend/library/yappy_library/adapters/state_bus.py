"""One signal that says "the state moved, go read it again".

This is the half of a push that is not the transport. The transport is the SSE
endpoint in :mod:`yappy_api.routes.workflow`; this is what lets that endpoint stop
asking and start listening.

**Why it exists.** The screen used to ask the same question every 1.5s — *did the
supervisor write a verdict?* — and be told "no" up to twenty times while a tunnel
finished booting. Asking is what a push removes, and the answer has to come from
somewhere: the writers of the tracker already know when they write. There are
exactly three of them inside the web process (a start, a stop, a supervisor
verdict), and all three can say so.

**What it deliberately does not cover.** Two changes have no event at all, and no
amount of bus fixes that:

- a tunnel someone started **from the terminal** is written by another process, and
- a process killed by something outside (a crash, a ``taskkill``) writes nothing.

Neither is a change the server hears about. Both are still *true*, and the truth
lives in the OS rather than in the tracker files, so reading it stays a periodic
operation: the stream carries a watchdog on top of this signal. That is why the
endpoint's loop is "wait for a wake, or five seconds, whichever comes first" and not
just "wait for a wake".

**No lifecycle.** The app runs with ``lifespan="off"`` — there is a test that fails
the build if a startup hook appears, because with the protocol off it would be
ignored in silence. So there is no startup and no shutdown here: a subscriber
registers when a connection opens and drops when its generator is closed or its
process dies. A dead process takes its queues with it, which is the only teardown
that can be trusted without a hook.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

#: What a wake carries. Not the state: readers differ in environment and cluster, so
#: the snapshot has to be computed per reader. The bus says *when*, never *what*.
WAKE = "changed"


@dataclass(eq=False)
class Subscription:
    """One connected reader, and the queue it will be woken through."""

    bus: StateBus
    #: The loop that owns :attr:`queue`. Captured at subscribe time because
    #: ``publish`` is called from threads that have no running loop of their own.
    loop: asyncio.AbstractEventLoop
    #: Depth of one, on purpose: a burst of ten writes has to coalesce into a single
    #: re-read. A full queue means "there is already a wake pending", and the one
    #: thing this must never do is make the reader answer ten times for one burst.
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1))

    def wake(self) -> None:
        """Make the reader run its loop once more, unless it already can."""

        if not self.queue.full():
            self.queue.put_nowait(WAKE)

    def close(self) -> None:
        """Drop this subscription. Idempotent: the generators may close twice."""

        self.bus.unsubscribe(self)


class StateBus:
    """A coalescing fan-out from "something changed" to "go look"."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[Subscription] = set()

    def subscribe(self) -> Subscription:
        """Register a reader. Call it from inside the event loop."""

        sub = Subscription(bus=self, loop=asyncio.get_running_loop())
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    def publish(self) -> None:
        """Wake every reader. Safe from any thread, including a supervisor's.

        Called after a tracker or catalog write. With no subscribers — the CLI, or a
        web nobody opened — this is a lock over an empty set: that is the whole
        price of the feature.
        """
        with self._lock:
            targets = list(self._subscribers)
        for sub in targets:
            try:
                sub.loop.call_soon_threadsafe(sub.wake)
            except RuntimeError:
                # The loop is closed, which means its process is going away. The
                # subscription is already useless and the set dies with the process,
                # so there is nothing to clean up and nothing to report.
                self.unsubscribe(sub)

    def __len__(self) -> int:
        """How many readers are connected."""

        with self._lock:
            return len(self._subscribers)


#: The one bus. A module-level singleton because the writers are functions in other
#: modules and a DI graph for "who is listening" is more wiring than the feature is
#: worth — and a second bus would mean a publish that reaches nobody, which presents
#: as "the push does not work".
state_bus = StateBus()