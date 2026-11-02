"""Database authentication and local credential-file adapter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import botocore.session

from yappy_library.adapters.file_io import file_lock, write_atomic
from yappy_library.config import Config, aws_port_of
from yappy_library.adapters.logging import die, success, warn
from yappy_library.paths import project_config_dir


def resolve_db_user(cfg: Config, local: dict[str, str] | None = None) -> str:
    """El usuario de la base, y de dónde sale.

    ``DB_USER`` es el nombre explícito de la base; ``AWS_USER`` es el del
    recurso de AWS, y porhistorical funciona como usuario de MySQL. Los dos
    eran válidos, pero cada caller aceptaba un juego distinto: la conexión
    aceptaba ``DB_USER`` y el token exigía ``AWS_USER``. Un environment
    configurado sólo con ``DB_USER`` —que es lo que documenta el template—
    fallaba al pedir el token.

    Ahora hay un solo lugar que decide, y los dos caminos aceptan lo mismo.
    """
    local = local or {}
    return (
        cfg.get("DB_USER")
        or local.get("DB_USER")
        or cfg.get("AWS_USER")
        # `aws_user` es la property de `Config`, que es lo mismo que lo de
        # arriba. Se lee de las dos formas porque no todos los que llegan
        # acá son un `Config`: hay dobles de prueba que sólo tienen una.
        or getattr(cfg, "aws_user", None)
        or ""
    )


def generate_token(cfg: Config) -> str:
    """Generate an RDS IAM auth token, falling back to the AWS CLI."""
    try:
        bc_session = botocore.session.Session(profile=cfg.profile)
        rds = bc_session.create_client("rds", region_name=cfg.require("AWS_REGION"))
        token = rds.generate_db_auth_token(
            DBHostname=cfg.require("AWS_HOST"),
            Port=aws_port_of(cfg),
            DBUsername=resolve_db_user(cfg),
            Region=cfg.require("AWS_REGION"),
        )
        if token:
            return token
    except Exception as exc:
        warn(f"botocore token failed, trying fallback: {exc}")

    cmd = [
        sys.executable,
        "-m",
        "awscli",
        "rds",
        "generate-db-auth-token",
        "--hostname",
        cfg.require("AWS_HOST"),
        "--port",
        str(aws_port_of(cfg)),
        "--username",
        resolve_db_user(cfg),
        "--region",
        cfg.require("AWS_REGION"),
        "--profile",
        cfg.profile,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die(f"Failed to generate DB token: {result.stderr.strip()}")
    return result.stdout.strip()


def _clipboard(text: str) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text, text=True, check=False)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=False)
        else:
            subprocess.run(
                ["xclip", "-selection", "clipboard"], input=text, text=True, check=False
            )
    except Exception:
        pass


def local_env_values(token: str, cfg: Config | None = None) -> dict[str, str]:
    """Las variables que se escriben en `.env.local`, en orden.

    Los nombres son el contrato de las pruebas locales: no se renombran.
    """
    cfg = cfg or Config()
    region = cfg.region
    host = "localhost"
    port = str(cfg.db_port)
    # La URL va ya armada: los servicios Java la pasan tal cual a
    # `setJdbcUrl`, y nadie reemplaza un nombre de variable dentro del texto.
    jdbc_url = f"jdbc:mysql://{host}:{port}"
    return {
        # Usuario y puerto salen de la config del ambiente: en AWS el usuario
        # es el `AWS_USER` de `env.base`; en local, el `DB_USER` de `env.local`.
        "DATABASE_USR": resolve_db_user(cfg),
        "DATABASE_HST": host,
        "DATABASE_PRT": port,
        "DATABASE_TMT": "60000",
        "AWS_REGION": region,
        "REGION": region,
        "ENV": cfg.env or "",
        "DATABASE_PWD": token,
        "DATABASE_HST_URL_WRITE": jdbc_url,
        "DATABASE_HST_URL_READ": jdbc_url,
    }


def write_local_env(
    token: str, env_local: Path | None = None, cfg: Config | None = None
) -> None:
    """Persist a generated database token to the project-local env file.

    Cada clave se actualiza en su línea: el archivo se reescribía entero desde
    un parse ingenuo, y eso perdía los comentarios y el orden de las claves.
    """
    if env_local is None:
        env_local = project_config_dir() / ".env.local"

    try:
        with file_lock(env_local):
            try:
                text = env_local.read_text(encoding="utf-8") if env_local.exists() else ""
            except OSError:
                text = ""

            lines = text.splitlines()
            pending = local_env_values(token, cfg)
            for index, line in enumerate(lines):
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key = stripped.split("=", 1)[0].strip()
                if key in pending:
                    lines[index] = f"{key}={pending.pop(key)}"

            if pending:
                if lines and lines[-1].strip():
                    lines.append("")
                lines.extend(f"{key}={value}" for key, value in pending.items())

            write_atomic(env_local, "\n".join(lines) + "\n")
            if os.name != "nt":
                try:
                    os.chmod(env_local, 0o600)
                except OSError:
                    pass
        success(f"Token saved to {env_local}")
    except Exception as exc:
        # El fallo se reporta y se corta: seguir y decir "Token copied to
        # clipboard" como si nada era mentira, y `db up` seguía creyendo que
        # el token quedó persistido.
        warn(f"Could not write {env_local}: {exc}")
        return

    _clipboard(token)
    success("Token copied to clipboard")
