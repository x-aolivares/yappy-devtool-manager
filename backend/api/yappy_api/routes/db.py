"""Database diff and SQL execution endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from yappy_library.adapters.database.connection import SyncError, connect
from yappy_library.application.database.sync import db_objects as obj
from yappy_library.application.database.sync import ddl
from yappy_library.application.database.sync import diff as db_diff
from yappy_library.application.database.sync import exec as syncexec
from yappy_library.application.database.sync import migrate as dbmig
from yappy_library.application.database.sync import query as dbquery
from ..deps import env_config
from ..schemas import (
    CompileRequest,
    CompileResponse,
    DbDiffRequest,
    DbObjectsResponse,
    DiffResponse,
    ExecuteRequest,
    ExecuteSqlResponse,
    MigrationRequest,
    MigrationResponse,
    QueryRequest,
    QueryResponse,
    SchemasResponse,
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
                    script = ddl.replace_procedure_script(code_b, schema, name)
                    notes = [
                        "El stored procedure difiere — se regenera con "
                        "DROP + CREATE (MySQL no tiene CREATE OR REPLACE "
                        "para procedures)."
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


@router.get("/api/db/schemas", operation_id="list_db_schemas", response_model=SchemasResponse)
def api_db_schemas(env: str):
    """List the user schemas of an environment, to feed the schema picker."""
    cfg = env_config(env)
    try:
        with connect(cfg) as conn:
            return {"env": env, "schemas": obj.list_schemas(conn)}
    except SyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de base de datos: {exc}") from exc


@router.get("/api/db/objects", operation_id="list_db_objects", response_model=DbObjectsResponse)
def api_db_objects(env: str, schema: str, object_type: str):
    """List the tables or stored procedures of one schema, to feed the object picker.

    The schema comes from :func:`api_db_schemas`, so it is already free of system
    schemas; the connection cost is the same one that listing schemas paid.
    """
    if object_type not in ("table", "procedure"):
        raise HTTPException(status_code=400, detail="object_type debe ser 'table' o 'procedure'")
    if not schema.strip():
        raise HTTPException(status_code=400, detail="Completá el schema.")

    schema = schema.strip()
    cfg = env_config(env)
    lister = obj.list_tables if object_type == "table" else obj.list_procedures
    try:
        with connect(cfg) as conn:
            return {
                "env": env,
                "schema_name": schema,
                "object_type": object_type,
                "objects": lister(conn, schema),
            }
    except SyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de base de datos: {exc}") from exc


@router.post("/api/execute/sql", operation_id="execute_sql", response_model=ExecuteSqlResponse)
def api_execute_sql(req: ExecuteRequest):
    """Run a SQL/DDL script against an environment.

    This is the only write path: ``/api/compile`` merely generates. ``code`` is
    whatever the user is looking at, so it may be a generated script, an edited
    version of it, or free-form SQL pasted from anywhere.
    """
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


@router.post("/api/compile", operation_id="compile_db_object", response_model=CompileResponse)
def api_compile(req: CompileRequest):
    """Generate the script that would take an object from source into destination.

    Nothing is written here: both environments are opened read-only to compare
    them and the script comes back in the response. The caller decides whether to
    send it to ``/api/execute/sql``, possibly after editing it.

    A stored procedure is recreated wholesale from the source definition
    (``CREATE OR REPLACE``). A table is not overwritten: only the columns and
    indexes that differ are emitted, as ``ALTER TABLE``, so destination data and
    its identity survive.

    ``code_b`` (source) and ``code_a`` (destination) come back for every branch
    so the caller can seed the editor even when there is no script to apply:
    ``script`` stays ``None`` when both sides are equal and ``""`` when the DDL
    text differs but the structure does not.
    """
    if req.env_a == req.env_b:
        raise HTTPException(status_code=400, detail="El origen y el destino deben ser distintos")
    if req.object_type not in ("table", "procedure"):
        raise HTTPException(status_code=400, detail="object_type debe ser 'table' o 'procedure'")
    if not req.schema_name.strip() or not req.object_name.strip():
        raise HTTPException(status_code=400, detail="Completá el schema y el nombre del objeto.")

    cfg_b = env_config(req.env_b)  # origen
    cfg_a = env_config(req.env_a)  # destino
    schema, name = req.schema_name.strip(), req.object_name.strip()
    code_a = None

    try:
        with connect(cfg_b) as conn_b, connect(cfg_a) as conn_a:
            if req.object_type == "procedure":
                code_b = obj.show_create_procedure(conn_b, schema, name)
                if code_b is None:
                    status, script = "none", None
                    notes = [f"El stored procedure no existe en {req.env_b} (origen)."]
                else:
                    code_a = obj.show_create_procedure(conn_a, schema, name)
                    status, script = "different", ddl.replace_procedure_script(code_b, schema, name)
                    notes = [
                        f"Se recompila {schema}.{name} en {req.env_a} (destino) "
                        f"tomando la definición de {req.env_b} (origen)."
                    ]
            else:
                code_b = obj.show_create_table(conn_b, schema, name)
                if code_b is None:
                    status, script = "none", None
                    notes = [f"La tabla no existe en {req.env_b} (origen)."]
                else:
                    code_a = obj.show_create_table(conn_a, schema, name)
                    cols_a = obj.table_columns(conn_a, schema, name)
                    idx_a = obj.table_indexes(conn_a, schema, name)

                    if not cols_a:
                        status, script = "missing_in_a", ddl.create_table_script(code_b)
                        notes = [
                            f"La tabla no existe en {req.env_a} (destino): "
                            f"se crea desde {req.env_b} (origen)."
                        ]
                    else:
                        cols_b = obj.table_columns(conn_b, schema, name)
                        idx_b = obj.table_indexes(conn_b, schema, name)
                        col_ops, index_ops = db_diff.diff_tables(cols_a, cols_b, idx_a, idx_b)

                        same_ddl = (
                            code_a is not None
                            and obj.normalize_ddl(code_a) == obj.normalize_ddl(code_b)
                        )
                        if same_ddl and not col_ops and not index_ops:
                            status, script = "equal", None
                            notes = [
                                "Sin cambios: la tabla del destino ya es igual a la del origen."
                            ]
                        else:
                            status = "different"
                            script = ddl.alter_table_script(schema, name, col_ops, index_ops)
                            if not script:
                                notes = [
                                    "El texto del DDL difiere pero la estructura "
                                    "(columnas e índices) es idéntica: no hay nada que aplicar."
                                ]
                            else:
                                notes = [
                                    f"Se actualizan columnas e índices de {schema}.{name} "
                                    f"en {req.env_a} (destino) para igualarla a {req.env_b}."
                                ]

        return {
            "env_b": req.env_b,
            "env_a": req.env_a,
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


@router.post("/api/query", operation_id="query_db", response_model=QueryResponse)
def api_query(req: QueryRequest):
    """Run one read-only statement and return its rows."""
    cfg = env_config(req.env)
    try:
        result = dbquery.run_select(cfg, req.code, limit=req.limit)
    except (dbquery.QueryError, SyncError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de base de datos: {exc}") from exc

    return {
        "env": req.env,
        "columns": result.columns,
        "rows": result.rows,
        "total": result.total,
        "truncated": result.truncated,
        "ms": result.ms,
    }


@router.post(
    "/api/migrate", operation_id="migrate_db_data", response_model=MigrationResponse
)
def api_migrate(req: MigrationRequest):
    """Migrate every table involved in a query, honouring its joins and filters.

    The query is expanded into one SELECT per table; each one's rows are copied
    with ``REPLACE INTO``. With ``dry_run`` nothing is written: the response only
    reports how many rows each table would contribute.
    """
    if req.env_a == req.env_b:
        raise HTTPException(status_code=400, detail="El origen y el destino deben ser distintos")
    if not req.dry_run and not req.confirm:
        raise HTTPException(
            status_code=400, detail="La migración requiere confirmación (confirm=true)."
        )

    cfg_b = env_config(req.env_b)  # origen
    cfg_a = env_config(req.env_a)  # destino

    try:
        plan = dbmig.parse_select(req.code, default_schema=req.default_schema)
        results = dbmig.migrate(cfg_b, cfg_a, plan, dry_run=req.dry_run)
    except (dbmig.MigrationError, dbquery.QueryError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de base de datos: {exc}") from exc

    return {
        "env_b": req.env_b,
        "env_a": req.env_a,
        "dry_run": req.dry_run,
        "tables": [r.to_dict() for r in results],
        "notes": plan.notes,
        "ok_count": sum(1 for r in results if r.ok),
        "err_count": sum(1 for r in results if not r.ok),
    }
