"""Database diff and SQL execution endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...sync import db_objects as obj
from ...sync import ddl
from ...sync import diff as db_diff
from ...sync import exec as syncexec
from ...sync.conn import SyncError, connect
from ..deps import env_config
from ..schemas import (
    DbDiffRequest,
    DiffResponse,
    ExecuteRequest,
    ExecuteSqlResponse,
)

router = APIRouter(tags=["db"])


def _table_structure(conn, schema: str, name: str):
    return (
        obj.table_columns(conn, schema, name),
        obj.table_indexes(conn, schema, name),
    )


@router.post("/api/db/diff", operation_id="diff_db_object", response_model=DiffResponse)
def api_db_diff(req: DbDiffRequest):
    if req.env_a == req.env_b:
        raise HTTPException(status_code=400, detail="env_a y env_b deben ser distintos")
    if req.object_type not in ("table", "procedure"):
        raise HTTPException(status_code=400, detail="object_type debe ser 'table' o 'procedure'")

    cfg_a = env_config(req.env_a)
    cfg_b = env_config(req.env_b)

    try:
        with connect(cfg_a) as conn_a, connect(cfg_b) as conn_b:
            schema, name = req.schema_name, req.object_name

            if req.object_type == "procedure":
                code_a = obj.show_create_procedure(conn_a, schema, name)
                code_b = obj.show_create_procedure(conn_b, schema, name)
                col_ops, index_ops = [], []
            else:
                code_a = obj.show_create_table(conn_a, schema, name)
                code_b = obj.show_create_table(conn_b, schema, name)
                cols_a, idx_a = _table_structure(conn_a, schema, name)
                cols_b, idx_b = _table_structure(conn_b, schema, name)
                col_ops, index_ops = db_diff.diff_tables(cols_a, cols_b, idx_a, idx_b)

            if code_a is None and code_b is None:
                status = "none"
                script = None
                notes = ["El objeto no existe en ninguna de las dos regiones."]
            elif code_a is None:
                status = "missing_in_a"
                if req.object_type == "procedure":
                    script = ddl.create_procedure_script(code_b)
                else:
                    script = ddl.create_table_script(code_b)
                notes = [f"Existe solo en {req.env_b} — debe crearse en {req.env_a}."]
            elif code_b is None:
                status = "missing_in_b"
                if req.include_deletes:
                    script = (
                        ddl.drop_procedure_script(schema, name)
                        if req.object_type == "procedure"
                        else ddl.drop_table_script(schema, name)
                    )
                    notes = [
                        f"Existe en {req.env_a} (destino) pero no en {req.env_b} (origen) — "
                        "se elimina para que la región destino quede igual a la de origen."
                    ]
                else:
                    script = None
                    notes = [
                        f"Existe en {req.env_a} (destino) pero no en {req.env_b} (origen) — "
                        "no hay nada que sincronizar (origen → destino). "
                        "Marcá la opción 'Incluir eliminaciones' para generar el DROP."
                    ]
            elif (
                obj.normalize_ddl(code_a) == obj.normalize_ddl(code_b)
                and not col_ops
                and not index_ops
            ):
                status = "equal"
                script = None
                notes = ["Sin cambios."]
            else:
                status = "different"
                if req.object_type == "procedure":
                    script = ddl.replace_procedure_script(code_b)
                    notes = [
                        "El stored procedure difiere — se regenera con "
                        "CREATE OR REPLACE (Aurora MySQL 3 / MySQL 8; "
                        "para MySQL 5.7 ejecutar DROP + CREATE)."
                    ]
                else:
                    script = ddl.alter_table_script(schema, name, col_ops, index_ops)
                    if not script:
                        notes = [
                            "El texto del DDL difiere pero la estructura "
                            "(columnas e índices) es idéntica "
                            "(p. ej. contador AUTO_INCREMENT)."
                        ]
                    else:
                        notes = ["La tabla difiere — se aplicaron los cambios de columnas e índices."]

            return {
                "env_a": req.env_a,
                "env_b": req.env_b,
                "object_type": req.object_type,
                "schema_name": schema,
                "object_name": name,
                "status": status,
                "code_a": code_a,
                "code_b": code_b,
                "script": script,
                "notes": notes,
            }
    except SyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de base de datos: {exc}") from exc


@router.post("/api/execute/sql", operation_id="execute_sql", response_model=ExecuteSqlResponse)
def api_execute_sql(req: ExecuteRequest):
    if req.object_type not in ("table", "procedure"):
        raise HTTPException(status_code=400, detail="object_type debe ser 'table' o 'procedure'")
    if not req.code or not req.code.strip():
        raise HTTPException(status_code=400, detail="El código a ejecutar no puede estar vacío.")

    cfg = env_config(req.env)

    try:
        results = syncexec.execute_sql(cfg, req.schema_name.strip(), req.code)
    except syncexec.SyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de base de datos: {exc}") from exc

    return {
        "env": req.env,
        "object_type": req.object_type,
        "schema_name": req.schema_name,
        "results": [r.__dict__ for r in results],
        "ok_count": sum(1 for r in results if r.ok),
        "err_count": sum(1 for r in results if not r.ok),
    }