import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import settings
from .db import check_database_connection
from . import docker_manager
from .routers import terminal
from . import pty_ws
from .workspace_sync import run_workspace_sync_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Starting {settings.SERVICE_NAME} v{settings.VERSION}...")
    await check_database_connection()
    await docker_manager.ensure_sandbox_image_built()
    await docker_manager.ensure_egress_proxy_image_built()
    sync_task = asyncio.create_task(run_workspace_sync_loop())
    yield
    sync_task.cancel()
    print(f"Shutting down {settings.SERVICE_NAME}...")


app = FastAPI(
    title="Terminal Sandbox Service",
    description="Owns Docker-socket access and per-terminal sandbox container lifecycle. Internal-only.",
    version=settings.VERSION,
    lifespan=lifespan,
)

app.include_router(terminal.router, tags=["Terminal"])
app.include_router(pty_ws.router, tags=["Terminal PTY"])


@app.get("/health")
async def health_check():
    return {"service": settings.SERVICE_NAME, "version": settings.VERSION, "status": "ok"}
