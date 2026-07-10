"""CRUD for TerminalSession rows. Modeled on RG_Auth/app/sessions.py."""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TerminalSession


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


async def get_session_by_terminal_id(
    terminal_id: str,
    db: AsyncSession,
) -> Optional[TerminalSession]:
    result = await db.execute(
        select(TerminalSession).where(TerminalSession.terminal_id == terminal_id)
    )
    return result.scalar_one_or_none()


async def create_or_get_terminal(
    terminal_id: str,
    user_id: UUID,
    db: AsyncSession,
    org_id: Optional[UUID] = None,
    project_id: Optional[str] = None,
) -> TerminalSession:
    """Idempotent: a second call with the same terminal_id returns the
    existing row rather than creating a duplicate. Container creation itself
    is handled by the caller via docker_manager, keyed off this row.
    """
    existing = await get_session_by_terminal_id(terminal_id, db)
    if existing is not None:
        return existing

    session = TerminalSession(
        terminal_id=terminal_id,
        user_id=user_id,
        org_id=org_id,
        project_id=project_id,
        status="creating",
        created_at=_utcnow(),
        last_active_at=_utcnow(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_sessions_for_user(
    user_id: UUID,
    db: AsyncSession,
) -> List[TerminalSession]:
    result = await db.execute(
        select(TerminalSession)
        .where(TerminalSession.user_id == user_id)
        .order_by(TerminalSession.created_at.desc())
    )
    return list(result.scalars().all())


async def list_running_sessions_with_project(db: AsyncSession) -> List[TerminalSession]:
    """Every currently-running session that has a project_id to sync
    against - used by the periodic workspace-sync reconciliation loop
    (see workspace_sync.py). Sessions with no project_id have nothing to
    sync to, so they're excluded up front rather than filtered per-tick.
    """
    result = await db.execute(
        select(TerminalSession).where(
            TerminalSession.status == "running",
            TerminalSession.project_id.isnot(None),
        )
    )
    return list(result.scalars().all())


async def touch_last_active(
    terminal_id: str,
    db: AsyncSession,
) -> None:
    session = await get_session_by_terminal_id(terminal_id, db)
    if session is not None:
        session.last_active_at = _utcnow()
        await db.commit()


async def mark_running(
    terminal_id: str,
    container_id: str,
    container_name: str,
    db: AsyncSession,
) -> None:
    session = await get_session_by_terminal_id(terminal_id, db)
    if session is not None:
        session.container_id = container_id
        session.container_name = container_name
        session.status = "running"
        session.last_active_at = _utcnow()
        await db.commit()


async def mark_closed(
    terminal_id: str,
    db: AsyncSession,
    status: str = "stopped",
) -> None:
    session = await get_session_by_terminal_id(terminal_id, db)
    if session is not None:
        session.status = status
        session.closed_at = _utcnow()
        await db.commit()
