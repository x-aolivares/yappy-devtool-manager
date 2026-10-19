"""That the server is going down — what open streams need in order to let go.

Uvicorn's graceful shutdown waits for every connection to close, and an SSE
connection never closes on its own: its generator loops forever waiting for
something to happen, and "the server is shutting down" is not something any of
the writers announces. So the Ctrl+C sat in ``Waiting for connections to close.
(CTRL+C to force quit)`` for as long as the browser tab stayed open — which is
the whole session, because the tab is exactly where the developer is looking
while they decide whether the server is stuck. One more Ctrl+C killed it, and
the terminal looked hung until they did.

This flag is the announcement. ``uvicorn.Server.handle_exit`` is the first
moment anyone in the process learns the signal arrived — uvicorn installs its
own handler while it serves and does not chain to the previous one (see
``run.py``) — and the flag set there is read at the top of both stream loops.
It is set *before* uvicorn starts closing anything, so the streams let go while
the graceful shutdown is still waiting for them.

**No lifecycle.** Same constraint as the state bus: the app runs with
``lifespan="off"``, so there is no shutdown hook to hang this on. The hook
belongs to the launcher, which is the one place that talks to uvicorn's server
object; this module is only the flag they share.
"""

from __future__ import annotations

import threading

from yappy_library.adapters.state_bus import state_bus

#: The flag. A module-level ``Event`` and not a boolean because the readers are
#: async generators in other modules and cross-thread visibility is the whole
#: point: the signal handler runs on the loop's thread, and what it must produce
#: is a wake on that same loop.
_flag = threading.Event()


def begin() -> None:
    """Mark the server as going down, and wake every connected stream at once.

    Idempotent: uvicorn calls this again on a second Ctrl+C — the force path —
    and that does not need a second announcement.

    The wake matters as much as the flag. A stream waiting on the bus would
    otherwise notice only on its next watchdog tick — up to five seconds after
    the process was already trying to exit — and the point of the flag is that
    the exit is immediate.
    """
    if _flag.is_set():
        return
    _flag.set()
    state_bus.publish()


def is_set() -> bool:
    """Whether the server is going down. A stream that sees True returns."""

    return _flag.is_set()
