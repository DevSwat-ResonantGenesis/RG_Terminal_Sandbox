"""Internal-only REST API for terminal container lifecycle. Not internet
reachable - only other services on the docker network call this (the gateway,
once Phase 1 wires the websocket proxy). Guarded by x-internal-service-key,
same pattern as RG_Auth/app/byok_routes.py's internal endpoints.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from .. import sessions as sessions_crud
from .. import docker_manager
from ..byok import fetch_anthropic_key

router = APIRouter()


def _require_internal(request: Request) -> None:
    internal_key = request.headers.get("x-internal-service-key")
    if internal_key != settings.INTERNAL_SERVICE_KEY and settings.ENVIRONMENT != "development":
        raise HTTPException(status_code=403, detail="Internal endpoint - access denied")


class CreateTerminalRequest(BaseModel):
    terminal_id: str
    user_id: str
    org_id: str | None = None
    project_id: str | None = None


def _session_to_dict(session) -> dict:
    return {
        "id": str(session.id),
        "terminal_id": session.terminal_id,
        "user_id": str(session.user_id),
        "org_id": str(session.org_id) if session.org_id else None,
        "project_id": session.project_id,
        "container_id": session.container_id,
        "git_repo_url": session.git_repo_url,
        "status": session.status,
        "transcript_storage_key": session.transcript_storage_key,
        "transcript_storage_bucket": session.transcript_storage_bucket,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "last_active_at": session.last_active_at.isoformat() if session.last_active_at else None,
        "closed_at": session.closed_at.isoformat() if session.closed_at else None,
    }


@router.post("/internal/terminals")
async def create_terminal(
    body: CreateTerminalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create-or-reuse the persistent container for terminal_id. Idempotent -
    calling this twice for the same terminal_id never creates a second
    container (see docker_manager.create_container).
    """
    _require_internal(request)

    try:
        user_uuid = UUID(body.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    org_uuid = UUID(body.org_id) if body.org_id else None

    session = await sessions_crud.create_or_get_terminal(
        terminal_id=body.terminal_id,
        user_id=user_uuid,
        db=db,
        org_id=org_uuid,
        project_id=body.project_id,
    )

    if session.user_id != user_uuid:
        # terminal_id collision across users - never let a second user attach
        # to someone else's container.
        raise HTTPException(status_code=403, detail="terminal_id owned by another user")

    anthropic_key = await fetch_anthropic_key(body.user_id)
    container_id = await docker_manager.create_container(body.terminal_id, body.user_id, anthropic_key)
    container_name = docker_manager.container_name_for(body.terminal_id)
    await sessions_crud.mark_running(body.terminal_id, container_id, container_name, db)

    session = await sessions_crud.get_session_by_terminal_id(body.terminal_id, db)
    return _session_to_dict(session)


@router.get("/internal/terminals/{terminal_id}")
async def get_terminal(
    terminal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_internal(request)
    session = await sessions_crud.get_session_by_terminal_id(terminal_id, db)
    if session is None:
        raise HTTPException(status_code=404, detail="Terminal not found")
    return _session_to_dict(session)


@router.delete("/internal/terminals/{terminal_id}")
async def delete_terminal(
    terminal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_internal(request)
    session = await sessions_crud.get_session_by_terminal_id(terminal_id, db)
    if session is None:
        raise HTTPException(status_code=404, detail="Terminal not found")

    await docker_manager.stop_container(terminal_id)
    await docker_manager.remove_container(terminal_id)
    await sessions_crud.mark_closed(terminal_id, db, status="stopped")

    return {"success": True, "terminal_id": terminal_id}


@router.post("/internal/terminals/{terminal_id}/heartbeat")
async def heartbeat(
    terminal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_internal(request)
    await sessions_crud.touch_last_active(terminal_id, db)
    return {"success": True}
