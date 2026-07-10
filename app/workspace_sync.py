"""Periodic reconciliation loop: pushes /workspace changes out to Gateway's
file API every few seconds for every running session, instead of only at
session teardown (which is all the original sync-out did). Runs as a
background asyncio task from this service's own lifespan - it can't be a
push from inside the sandbox container, because terminal_egress_net has no
route back to this service (by design, see docker_manager.py's network
comments), so the controller has to poll instead.
"""
import asyncio
import logging

from .db import SessionLocal
from . import sessions as sessions_crud
from . import docker_manager
from . import gateway_files

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 10


async def _sync_one(session) -> None:
    container_id = await docker_manager.find_container_id(session.terminal_id)
    if not container_id:
        return

    if not await docker_manager.workspace_changed_since_last_sync(container_id):
        return

    files = await docker_manager.copy_workspace_out(container_id)
    for f in files:
        await gateway_files.write_project_file(
            session.project_id, f["file_path"], f["content"], str(session.user_id)
        )
    await docker_manager.mark_workspace_synced(container_id)


async def _tick() -> None:
    async with SessionLocal() as db:
        running = await sessions_crud.list_running_sessions_with_project(db)

    for session in running:
        try:
            await _sync_one(session)
        except Exception as e:
            logger.warning(f"workspace_sync: failed for terminal_id={session.terminal_id}: {e}")


async def run_workspace_sync_loop() -> None:
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.warning(f"workspace_sync: tick failed: {e}")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
