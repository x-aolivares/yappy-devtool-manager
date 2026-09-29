import typer

from ..api.kafka import KafkaService
from ..config import Config
from ..deprecation import warn_deprecated
from ..logger import die

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
    _service().up(action, detach=detach)


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
    _service().down(target)
