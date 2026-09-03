from __future__ import annotations

from .logger import warn

_OLD_TO_NEW: dict[str, str] = {
    "db up":        "run db",
    "db refresh":   "run db --refresh",
    "ssm connect":  "run tunnel",
    "ssm producer": "run tunnel producer <env>",
    "ssm kafdrop":  "run tunnel kafdrop",
    "ssm databricks": "run tunnel databricks",
    "ssm kill":     "stop tunnel",
    "kafka up":     "run kafka",
    "kafka down":   "stop kafka",
    "aws session":  "login aws",
    "aws mfa":      "login mfa",
    "workflow debug-local": "run workflow",
    "workflow executor":    "run workflow <env>",
}


def warn_deprecated(old_cmd: str, new_cmd: str, extra_hint: str = ""):
    msg = (
        f"[yellow]DEPRECATED:[/yellow] '[bold]yappy {old_cmd}[/bold]' "
        f"will be removed in v1.0. Use '[bold]yappy {new_cmd}[/bold]' instead."
    )
    if extra_hint:
        msg += f" {extra_hint}"
    warn(msg)
