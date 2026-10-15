import typer

from yappy_library.adapters.kafka.service import KafkaService, KafkaUnavailable
from yappy_library.config import Config
from ..deprecation import warn_deprecated
from yappy_library.adapters.logging import die

app = typer.Typer(help="Local Kafka management")


def _service() -> KafkaService:
    return KafkaService(Config())


@app.command()
def up(
    action: str = typer.Argument(..., help="server, ui, or clean"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    quiet_deprecation: bool = False,
):
    """Start local Kafka (server), UI (kafdrop), or clean (reset storage)."""
    if not quiet_deprecation:
        warn_deprecated("kafka up", "run kafka")
    if action not in ("server", "ui", "clean"):
        die("Invalid action. Use: server, ui, or clean")
    # `KafkaUnavailable` is how the service says "this machine cannot run Kafka"
    # now that it no longer calls `die()` itself — the web calls it from inside
    # uvicorn and a `SystemExit` there is a silent server shutdown. From a
    # terminal that is still an exit, so the exit is re-made right here with the
    # same message and the same code the service used to use.
    try:
        _service().up(action, detach=detach)
    except KafkaUnavailable as exc:
        die(exc.reason)


@app.command()
def down(
    target: str = typer.Argument("server", help="server or ui"),
    quiet_deprecation: bool = False,
):
    """Stop local Kafka server or UI."""
    if not quiet_deprecation:
        warn_deprecated("kafka down", "stop kafka")
    if target not in ("server", "ui"):
        die("Invalid target. Use: server or ui")
    try:
        _service().down(target)
    except KafkaUnavailable as exc:
        die(exc.reason)
