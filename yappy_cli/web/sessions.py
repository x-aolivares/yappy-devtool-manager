"""Session store for batch parameter review (SQLite, local only).

Sessions are review ledgers: they keep the list of parameters a user pasted
from their spreadsheet, the destination/src region pair, and per item the diff
snapshot and outcome (pendiente / revisado / aplicado / saltado). Nothing here
ever talks to AWS — execution happens in the existing params-diff flows; this
module just records what happened.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

VALID_STATUSES = ("pendiente", "revisado", "aplicado", "saltado")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    env_a      TEXT NOT NULL,
    env_b      TEXT NOT NULL,
    service    TEXT NOT NULL DEFAULT 'ssm',
    title      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    service     TEXT NOT NULL DEFAULT 'ssm',
    is_secret   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pendiente',
    diff_json   TEXT,
    diff_err    TEXT,
    script      TEXT,
    preview     TEXT,
    notes       TEXT,
    visited_at  TEXT,
    applied_at  TEXT,
    updated_at  TEXT,
    UNIQUE(session_id, name)
);
"""


def _db_path() -> Path:
    override = os.environ.get("YAPPY_SESSIONS_DB")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "data" / "sessions.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _status_counts(items: list[sqlite3.Row]) -> dict[str, int]:
    counts = {s: 0 for s in VALID_STATUSES}
    for item in items:
        counts[item["status"]] += 1
    return counts


def _default_session_title(env_a: str, env_b: str, item_count: int) -> str:
    return f"{env_b} → {env_a} · {item_count} parámetros"


def _refresh_title_if_default(session_id: str, *, env_a: str | None = None, env_b: str | None = None, item_count: int | None = None) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT env_a, env_b, title FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"No existe la sesión '{session_id}'.")

    current_env_a = env_a if env_a is not None else row["env_a"]
    current_env_b = env_b if env_b is not None else row["env_b"]
    current_count = item_count if item_count is not None else 0
    default_title = _default_session_title(current_env_a, current_env_b, current_count)
    current_title = (row["title"] or "").strip()
    if current_title and re.fullmatch(rf"{re.escape(current_env_b)}\s*→\s*{re.escape(current_env_a)}\s*·\s*\d+\s*parámetros", current_title):
        with _connect() as conn:
            conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (default_title, session_id))
        return default_title
    return current_title or default_title


def _row_to_item(row: sqlite3.Row) -> dict:
    return {
        "name": row["name"],
        "position": row["position"],
        "service": row["service"],
        "is_secret": bool(row["is_secret"]),
        "status": row["status"],
        "diff_json": row["diff_json"],
        "diff_err": row["diff_err"],
        "script": row["script"],
        "preview": row["preview"],
        "notes": row["notes"],
        "visited_at": row["visited_at"],
        "applied_at": row["applied_at"],
        "updated_at": row["updated_at"],
    }


def _find_reusable(
    env_a: str, env_b: str, service: str, names: list[str]
) -> dict | None:
    """Return an existing session with the same env pair, service and exact
    parameter list (as a set), or None. Used to avoid duplicating sessions
    when the user re-reads the same spreadsheet list."""
    wanted = set(names)
    if len(wanted) != len(names):
        return None
    with _connect() as conn:
        rows = conn.execute(
            "SELECT s.id FROM sessions s "
            "JOIN session_items i ON i.session_id = s.id "
            "WHERE s.env_a = ? AND s.env_b = ? AND s.service = ? "
            "GROUP BY s.id HAVING COUNT(DISTINCT i.name) = ?",
            (env_a, env_b, service, len(names)),
        ).fetchall()
        candidate_ids = [r["id"] for r in rows]
    for sid in candidate_ids:
        with _connect() as conn:
            got = conn.execute(
                "SELECT name FROM session_items WHERE session_id = ?", (sid,)
            ).fetchall()
        if {g["name"] for g in got} == wanted:
            return get_session(sid)
    return None


def create_session(
    env_a: str,
    env_b: str,
    keys: list[str],
    service: str = "ssm",
    title: str = "",
    alias: str = "",
    reuse: bool = False,
) -> dict:
    if env_a == env_b:
        raise ValueError("env_a y env_b deben ser distintos.")
    if service not in ("ssm", "secretsmanager"):
        raise ValueError("service debe ser 'ssm' o 'secretsmanager'.")

    names: list[str] = []
    for key in keys:
        name = str(key).strip()
        if not name:
            continue
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError("La lista de parámetros no puede estar vacía.")
    if len(names) > 200:
        raise ValueError(f"Máximo 200 parámetros por sesión (recibiste {len(names)}).")

    if reuse:
        existing = _find_reusable(env_a, env_b, service, names)
        if existing is not None:
            return existing

    session_id = (
        f"ses-{env_b}-{env_a}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    created = _now()
    final_title = (alias or title).strip() or f"{env_b} → {env_a} · {len(names)} parámetros"

    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at, env_a, env_b, service, title) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, created, env_a, env_b, service, final_title),
        )
        conn.executemany(
            "INSERT INTO session_items "
            "(session_id, position, name, service, status) VALUES (?, ?, ?, ?, 'pendiente')",
            [(session_id, i, name, service) for i, name in enumerate(names)],
        )

    return get_session(session_id)


def add_item(
    session_id: str,
    name: str,
    *,
    service: str | None = None,
    is_secret: bool | None = None,
    status: str = "pendiente",
    diff_json: str | None = None,
    diff_err: str | None = None,
    script: str | None = None,
    preview: str | None = None,
    notes: str | None = None,
) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(f"status debe ser uno de: {', '.join(VALID_STATUSES)}.")

    session = get_session(session_id)
    item_name = str(name).strip()
    if not item_name:
        raise ValueError("El nombre del ítem no puede estar vacío.")

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM session_items WHERE session_id = ? AND name = ?",
            (session_id, item_name),
        ).fetchone()
        if row is not None:
            return _row_to_item(row)

        final_service = (service or session["service"]).strip()
        if final_service not in ("ssm", "secretsmanager"):
            raise ValueError("service debe ser 'ssm' o 'secretsmanager'.")
        position = len(session["items"])
        conn.execute(
            "INSERT INTO session_items "
            "(session_id, position, name, service, is_secret, status, diff_json, diff_err, script, preview, notes, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                position,
                item_name,
                final_service,
                1 if is_secret else 0,
                status,
                diff_json,
                diff_err,
                script,
                preview,
                notes,
                _now(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM session_items WHERE session_id = ? AND name = ? ORDER BY position DESC LIMIT 1",
            (session_id, item_name),
        ).fetchone()

    _refresh_title_if_default(
        session_id,
        env_a=session["env_a"],
        env_b=session["env_b"],
        item_count=len(session["items"]) + 1,
    )

    return _row_to_item(row)


def generate_markdown_report(session_id: str) -> str:
    session = get_session(session_id)
    lines = [
        f"# {session['title']}",
        "",
        f"- ID: `{session['id']}`",
        f"- Ambientes: `{session['env_a']}` → `{session['env_b']}`",
        f"- Servicio: `{session['service']}`",
        f"- Total: `{len(session['items'])}` ítems",
        "",
    ]
    for status, count in session["status_counts"].items():
        if count:
            lines.append(f"- {status}: {count}")
    lines.append("")
    if not session["items"]:
        lines.append("Sin cambios registrados.")
        return "\n".join(lines) + "\n"

    lines.append("## Ítems")
    for item in session["items"]:
        lines.append(
            f"- `{item['name']}` — `{item['status']}` — servicio `{item['service']}` | secreto `{str(item['is_secret']).lower()}`"
        )
        if item.get("notes"):
            lines.append(f"  - Nota: {item['notes']}")
        if item.get("script"):
            lines.append(f"  - Script: `{item['script']}`")
    return "\n".join(lines) + "\n"


def list_sessions() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC, id DESC"
        ).fetchall()
        items_by_session: dict[str, list[sqlite3.Row]] = {}
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            item_rows = conn.execute(
                f"SELECT * FROM session_items WHERE session_id IN ({placeholders})",
                ids,
            ).fetchall()
            for item in item_rows:
                items_by_session.setdefault(item["session_id"], []).append(item)

    sessions = []
    for row in rows:
        items = items_by_session.get(row["id"], [])
        title = _refresh_title_if_default(
            row["id"],
            env_a=row["env_a"],
            env_b=row["env_b"],
            item_count=len(items),
        )
        sessions.append(
            {
                "id": row["id"],
                "title": title,
                "created_at": row["created_at"],
                "env_a": row["env_a"],
                "env_b": row["env_b"],
                "service": row["service"],
                "item_count": len(items),
                "status_counts": _status_counts(items),
            }
        )
    return sessions


def get_session(session_id: str) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No existe la sesión '{session_id}'.")
        items = conn.execute(
            "SELECT * FROM session_items WHERE session_id = ? ORDER BY position",
            (session_id,),
        ).fetchall()

    title = _refresh_title_if_default(
        row["id"],
        env_a=row["env_a"],
        env_b=row["env_b"],
        item_count=len(items),
    )

    return {
        "id": row["id"],
        "title": title,
        "created_at": row["created_at"],
        "env_a": row["env_a"],
        "env_b": row["env_b"],
        "service": row["service"],
        "status_counts": _status_counts(items),
        "items": [_row_to_item(i) for i in items],
    }


def delete_session(session_id: str) -> None:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    if cur.rowcount == 0:
        raise ValueError(f"No existe la sesión '{session_id}'.")


def update_item(
    session_id: str,
    name: str,
    *,
    status: str | None = None,
    service: str | None = None,
    is_secret: bool | None = None,
    diff_json: str | None = None,
    diff_err: str | None = None,
    script: str | None = None,
    preview: str | None = None,
    notes: str | None = None,
) -> dict:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(
            f"status debe ser uno de: {', '.join(VALID_STATUSES)} (recibiste '{status}')."
        )

    fields: dict[str, object] = {}
    if status is not None:
        fields["status"] = status
        if status == "applied" or status == "aplicado":
            fields["status"] = "aplicado"
            fields["applied_at"] = _now()
        elif status == "revisado":
            fields["visited_at"] = _now()
        elif status == "pendiente":
            fields["applied_at"] = None
        fields["updated_at"] = _now()
    if service is not None:
        fields["service"] = service
    if is_secret is not None:
        fields["is_secret"] = 1 if is_secret else 0
    if diff_json is not None:
        fields["diff_json"] = diff_json
    if diff_err is not None:
        fields["diff_err"] = diff_err
    if script is not None:
        fields["script"] = script
    if preview is not None:
        fields["preview"] = preview
    if notes is not None:
        fields["notes"] = notes
    if not fields:
        fields["updated_at"] = _now()

    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM session_items WHERE session_id = ? AND name = ?",
            (session_id, name),
        )
        item_row = cur.fetchone()
        if item_row is None:
            raise ValueError(
                f"No existe el ítem '{name}' en la sesión '{session_id}'."
            )

        # Never downgrade an already-applied item through the automatic
        # "revisado" snapshot (e.g. the re-diff fired after a successful exec).
        if status == "revisado" and item_row["status"] == "aplicado":
            status = None
            fields.pop("status", None)
            fields.pop("visited_at", None)
            fields.pop("applied_at", None)
            fields["updated_at"] = _now()

        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE session_items SET {cols} WHERE id = ?",
                [*fields.values(), item_row["id"]],
            )
        row = conn.execute(
            "SELECT * FROM session_items WHERE id = ?", (item_row["id"],)
        ).fetchone()

    return _row_to_item(row)