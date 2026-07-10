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
from .. import gateway_files
from .. import ssh_hosts
from .. import workspace_tokens
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

    # Opt-in SSH egress: only touches anything for a user who has explicitly
    # registered a host. Every other user gets the exact same shared-proxy
    # path as before - see docker_manager.create_egress_proxy.
    egress_proxy_url = None
    mount_ssh_identity = False
    registered_host = await ssh_hosts.get_registered_host(body.user_id)
    if registered_host:
        egress_proxy_url = await docker_manager.create_egress_proxy(
            body.terminal_id, registered_host["host"], registered_host["port"]
        )
        mount_ssh_identity = True

    # Scoped platform API token (agents:*/builder:*) - only meaningful when
    # this terminal is tied to a real workspace (body.project_id IS the
    # workspace_id once the frontend derives terminal_id from it, see
    # ORG_Frontend's terminalSession.ts). No workspace context, no token.
    workspace_token = None
    if body.project_id:
        workspace_token = await workspace_tokens.mint_workspace_token(body.user_id, body.project_id)

    container_id, created = await docker_manager.create_container(
        body.terminal_id, body.user_id, anthropic_key,
        egress_proxy_url=egress_proxy_url, mount_ssh_identity=mount_ssh_identity,
        workspace_token=workspace_token,
    )
    container_name = docker_manager.container_name_for(body.terminal_id)
    await sessions_crud.mark_running(body.terminal_id, container_id, container_name, db)

    # Sync the user's existing IDE project files into /workspace - only on
    # genuine creation (not a reconnect to an already-running container, which
    # would clobber any in-progress local changes with the last-synced copy).
    if created and body.project_id:
        files = await gateway_files.fetch_project_files(body.project_id, body.user_id)
        if files:
            await docker_manager.copy_files_into_container(container_id, files)

    # Refresh the platform-access CLAUDE.md on every connect, not just
    # genuine creation - most real sessions today are reconnects to a
    # container that predates this feature, so gating on `created` meant
    # it silently never wrote for anyone already using the terminal.
    # write_claude_md replaces its own marked section idempotently, so
    # this is safe to call every time without duplicating content.
    if workspace_token:
        await docker_manager.write_claude_md(container_id, workspace_token)

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

    # Sync /workspace back to the IDE's project storage before the container
    # (and its volume) is gone for good.
    if session.project_id:
        container_id = await docker_manager.find_container_id(terminal_id)
        if container_id:
            files = await docker_manager.copy_workspace_out(container_id)
            for f in files:
                await gateway_files.write_project_file(
                    session.project_id, f["file_path"], f["content"], str(session.user_id)
                )

    await docker_manager.stop_container(terminal_id)
    await docker_manager.remove_container(terminal_id)
    await docker_manager.remove_egress_proxy(terminal_id)
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


@router.post("/internal/ssh-keys/{user_id}")
async def get_or_create_ssh_key(user_id: str, request: Request):
    """Ensure this user has an SSH keypair in their persistent identity
    volume and return the public half, for display right after they
    register a host in account settings (see RG_Auth's UserSshHost). The
    private key is never included in this response - see
    docker_manager.ensure_ssh_keypair's docstring for where it actually lives.
    """
    _require_internal(request)
    public_key = await docker_manager.ensure_ssh_keypair(user_id)
    if not public_key:
        raise HTTPException(status_code=500, detail="Failed to generate SSH keypair")
    await ssh_hosts.report_fingerprint(user_id, public_key)
    return {"public_key": public_key}
