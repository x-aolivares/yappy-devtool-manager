"""Session review endpoints (SQLite-backed ledger)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from .. import sessions as S
from ..schemas import (
    CreateSessionRequest,
    DeleteResponse,
    SessionDetailResponse,
    SessionItemInfo,
    SessionsListResponse,
    UpdateSessionItemRequest,
)

router = APIRouter(tags=["sessions"])


@router.post(
    "/api/sessions",
    operation_id="create_session",
    response_model=SessionDetailResponse,
)
def api_sessions_create(req: CreateSessionRequest):
    try:
        return S.create_session(
            env_a=req.env_a,
            env_b=req.env_b,
            keys=req.keys,
            service=req.service,
            title=req.title,
            alias=req.alias,
            reuse=req.reuse,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error de sesión: {exc}") from exc


@router.get(
    "/api/sessions",
    operation_id="list_sessions",
    response_model=SessionsListResponse,
)
def api_sessions_list():
    return {"sessions": S.list_sessions()}


@router.get(
    "/api/sessions/{session_id}",
    operation_id="get_session",
    response_model=SessionDetailResponse,
)
def api_sessions_get(session_id: str, name: str | None = None, filter: str | None = None):
    try:
        session = S.get_session(session_id)
        q = (filter or name or "").strip().lower()
        if q:
            session["items"] = [
                item for item in session["items"] if q in item["name"].lower()
            ]
        return session
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/sessions/{session_id}/report.md",
    operation_id="get_session_report_markdown",
)
def api_sessions_report(session_id: str):
    try:
        markdown = S.generate_markdown_report(session_id)
        return Response(content=markdown, media_type="text/markdown; charset=utf-8")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete(
    "/api/sessions/{session_id}",
    operation_id="delete_session",
    response_model=DeleteResponse,
)
def api_sessions_delete(session_id: str):
    try:
        S.delete_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post(
    "/api/sessions/{session_id}/items",
    operation_id="update_session_item",
    response_model=SessionItemInfo,
)
def api_sessions_item_update(session_id: str, req: UpdateSessionItemRequest):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Ingresá el nombre del ítem.")
    if req.status is not None and req.status not in S.VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status debe ser uno de: {', '.join(S.VALID_STATUSES)}.",
        )
    try:
        return S.update_item(
            session_id,
            req.name,
            status=req.status,
            service=req.service,
            is_secret=req.is_secret,
            diff_json=req.diff_json,
            diff_err=req.diff_err,
            script=req.script,
            preview=req.preview,
            notes=req.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/api/sessions/{session_id}/items/create",
    operation_id="create_session_item",
    response_model=SessionItemInfo,
)
def api_sessions_item_create(session_id: str, req: UpdateSessionItemRequest):
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Ingresá el nombre del ítem.")
    if req.status is not None and req.status not in S.VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status debe ser uno de: {', '.join(S.VALID_STATUSES)}.",
        )
    try:
        session = S.get_session(session_id)
        item_name = req.name.strip()
        if any(item["name"] == item_name for item in session["items"]):
            raise HTTPException(
                status_code=409,
                detail=f"El ítem '{item_name}' ya existe en la sesión.",
            )
        return S.add_item(
            session_id,
            item_name,
            service=req.service,
            is_secret=req.is_secret,
            status=req.status or "pendiente",
            diff_json=req.diff_json,
            diff_err=req.diff_err,
            script=req.script,
            preview=req.preview,
            notes=req.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc