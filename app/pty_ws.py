"""PTY bridge — pty.fork() *this* service's own container (which has the
Docker CLI + docker.sock, per RG_Terminal_Sandbox/Dockerfile) and exec
`docker exec -it <container_id> /bin/bash` so the actual shell always runs
inside the hardened per-user sandbox container, never here. This is the
"Phase 1's pty_ws.py" referenced in docker_manager.py's module docstring.

Wire protocol (JSON text frames both ways, matches the old
RG_Axtention_IDE/app/pty_stream.py client contract):
  server -> client: {"type": "output", "data": "<text>"}
                    {"type": "error", "message": "<text>"}
  client -> server: {"type": "input", "data": "<text>"}
                    {"type": "resize", "cols": <int>, "rows": <int>}
"""
import asyncio
import fcntl
import json
import logging
import os
import pty
import struct
import termios

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .config import settings
from . import docker_manager

logger = logging.getLogger(__name__)

router = APIRouter()


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


@router.websocket("/internal/terminals/{terminal_id}/pty")
async def terminal_pty(websocket: WebSocket, terminal_id: str):
    key = websocket.query_params.get("key", "")
    if key != settings.INTERNAL_SERVICE_KEY and settings.ENVIRONMENT != "development":
        await websocket.close(code=4003)
        return

    await websocket.accept()

    container_id = await docker_manager.find_container_id(terminal_id)
    if not container_id:
        await websocket.send_json({"type": "error", "message": "Container not found"})
        await websocket.close(code=4004)
        return

    argv = await docker_manager.exec_shell_argv(container_id)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(argv[0], argv)
        os._exit(1)

    loop = asyncio.get_event_loop()
    output_queue: asyncio.Queue = asyncio.Queue()

    def _on_readable():
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        if not data:
            try:
                loop.remove_reader(fd)
            except Exception:
                pass
            output_queue.put_nowait(None)
            return
        output_queue.put_nowait(data)

    loop.add_reader(fd, _on_readable)

    async def pump_output():
        while True:
            data = await output_queue.get()
            if data is None:
                break
            try:
                await websocket.send_json({"type": "output", "data": data.decode("utf-8", errors="replace")})
            except Exception:
                break

    output_task = asyncio.create_task(pump_output())

    try:
        while True:
            text = await websocket.receive_text()
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            mtype = payload.get("type")
            if mtype == "input":
                os.write(fd, payload.get("data", "").encode())
            elif mtype == "resize":
                try:
                    _set_winsize(fd, int(payload.get("rows", 24)), int(payload.get("cols", 80)))
                except (OSError, ValueError):
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"terminal pty error terminal_id={terminal_id}: {e}")
    finally:
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        output_task.cancel()
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
