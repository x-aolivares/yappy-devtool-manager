from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.run_query import run_local_query, run_query
from ..container import Container, get_container
from ..schemas import QueryResultDTO, RunQueryRequest

router = APIRouter(prefix="/query", tags=["query"])


@router.post("/{env}", response_model=QueryResultDTO)
def post_query(
    env: str,
    body: RunQueryRequest,
    c: Container = Depends(get_container),
) -> QueryResultDTO:
    """Run a read-only statement against an environment.

    Only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH. Anything else raises
    UnsafeQueryError -> 400 with the reason.
    """
    if body.target == "local":
        result = run_local_query(c.mysql, body.sql, body.db_schema)
    else:
        if not body.db_schema:
            from ..domain.exceptions import UnsafeQueryError

            raise UnsafeQueryError("A schema is required to query an environment")
        result = run_query(c.environments, c.mysql, env, body.db_schema, body.sql)

    return QueryResultDTO(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        elapsed_ms=result.elapsed_ms,
        truncated=result.truncated,
    )
