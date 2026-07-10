"""Hardened per-terminal container lifecycle. Adapted from
RG_Code_Execution/app/executor.py's CodeExecutor (single-shot, network=none)
into a persistent-with-exec model: one container per terminal_id, kept alive
with `sleep infinity`, attached to via `docker exec` on demand.

Nothing in this service ever execs a shell directly on the host - the shell
always runs inside this hardened container. This is the concrete fix for the
isolation gap in the old RG_Axtention_IDE/app/pty_stream.py (pty.fork() +
os.execvp on the bare host).
"""
import asyncio
import hashlib
import logging
import os
from typing import Optional, Tuple

from .config import settings

logger = logging.getLogger(__name__)

CONTAINER_LABEL_TERMINAL = "com.resonant.terminal_id"
CONTAINER_LABEL_USER = "com.resonant.user_id"


def container_name_for(terminal_id: str) -> str:
    h = hashlib.sha256(terminal_id.encode()).hexdigest()[:16]
    return f"term_{h}"


async def _run(*args: str) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def find_container_id(terminal_id: str) -> Optional[str]:
    name = container_name_for(terminal_id)
    rc, out, _ = await _run(
        "docker", "ps", "-aq", "--filter", f"name=^{name}$",
    )
    out = out.strip()
    return out if rc == 0 and out else None


async def create_container(terminal_id: str, user_id: str, anthropic_key: Optional[str] = None) -> str:
    """Create the persistent sandbox container for a terminal_id. Idempotent:
    if a container with this terminal's name already exists, reuse it instead
    of creating a duplicate (this is what makes reconnects reuse state).
    `anthropic_key`, when provided, is only applied at creation time - it is
    not refreshed into an already-running container on reuse.
    """
    existing = await find_container_id(terminal_id)
    if existing:
        # Make sure it's actually running, not just present-but-stopped.
        rc, out, _ = await _run("docker", "inspect", "-f", "{{.State.Running}}", existing)
        if rc == 0 and out.strip() == "true":
            return existing
        await _run("docker", "start", existing)
        return existing

    name = container_name_for(terminal_id)
    docker_cmd = [
        "docker", "run",
        "-d",
        f"--name={name}",
        f"--label={CONTAINER_LABEL_TERMINAL}={terminal_id}",
        f"--label={CONTAINER_LABEL_USER}={user_id}",

        # Resource limits - larger than the short-lived snippet-exec baseline
        # since this runs an editor-grade session + Claude Code + npm.
        f"--memory={settings.SANDBOX_MEMORY_MB}m",
        f"--memory-swap={settings.SANDBOX_MEMORY_MB}m",
        f"--cpus={settings.SANDBOX_CPUS}",
        f"--pids-limit={settings.SANDBOX_PIDS_LIMIT}",

        # Security hardening - matches/exceeds RG_Code_Execution's CodeExecutor
        "--security-opt=no-new-privileges",
        "--cap-drop=ALL",
        f"--user={settings.SANDBOX_UID}",
        "--read-only",
        f"--tmpfs=/tmp:rw,noexec,nosuid,size={settings.SANDBOX_TMPFS_SIZE_MB}m",
        f"--tmpfs=/home/sandboxuser:rw,nosuid,size={settings.SANDBOX_TMPFS_SIZE_MB}m,uid={settings.SANDBOX_UID}",
        f"-v={name}_workspace:/workspace",

        # Phase 3: egress-restricted network (terminal_egress_net is
        # `internal: true` - no route out except through the dual-homed
        # terminal_egress_proxy squid container, and no route to app-network
        # either, so a sandbox container can't pivot to auth_service/billing/
        # Postgres). Do not point this at app-network or any non-internal
        # network without the egress proxy allowlist in place.
        f"--network={settings.SANDBOX_NETWORK}",
        f"-e=HTTPS_PROXY={settings.SANDBOX_EGRESS_PROXY_URL}",
        f"-e=HTTP_PROXY={settings.SANDBOX_EGRESS_PROXY_URL}",
        "-e=NO_PROXY=localhost,127.0.0.1",
    ]
    if anthropic_key:
        docker_cmd.append(f"-e=ANTHROPIC_API_KEY={anthropic_key}")

    docker_cmd += [settings.SANDBOX_IMAGE, "sleep", "infinity"]

    logger.info(f"Creating sandbox container for terminal_id={terminal_id}")
    rc, out, err = await _run(*docker_cmd)
    if rc != 0:
        raise RuntimeError(f"Failed to create sandbox container: {err}")

    return out.strip()


async def exec_shell_argv(container_id: str) -> list:
    """Argv for spawning an interactive shell inside the container via
    `docker exec`. The caller (Phase 1's pty_ws.py) wraps this with a
    controller-side PTY (pty.openpty()) purely to give `docker exec` a tty -
    the shell process itself always lives inside the isolated container.

    `docker exec -it` does NOT set TERM on its own - without it, full-screen
    TUI programs (like Claude Code's interactive prompts) can't look up
    terminfo capabilities for cursor movement/colors and emit garbled,
    unseparated output instead.
    """
    return ["docker", "exec", "-it", "-e", "TERM=xterm-256color", container_id, "/bin/bash"]


async def stop_container(terminal_id: str) -> bool:
    container_id = await find_container_id(terminal_id)
    if not container_id:
        return False
    await _run("docker", "stop", "-t", "5", container_id)
    return True


async def remove_container(terminal_id: str) -> bool:
    container_id = await find_container_id(terminal_id)
    if not container_id:
        return False
    await _run("docker", "rm", "-f", container_id)
    return True


async def list_labeled_container_ids() -> list:
    rc, out, _ = await _run(
        "docker", "ps", "-aq", "--filter", f"label={CONTAINER_LABEL_TERMINAL}",
    )
    if rc != 0:
        return []
    return [line for line in out.strip().splitlines() if line]


async def get_container_label(container_id: str, label: str) -> Optional[str]:
    rc, out, _ = await _run(
        "docker", "inspect", "-f", f"{{{{index .Config.Labels \"{label}\"}}}}", container_id,
    )
    out = out.strip()
    return out if rc == 0 and out else None


async def ensure_sandbox_image_built() -> None:
    """Build the sandbox-image tag on startup if it isn't already present.
    Keeps this service self-contained (no separate deploy-tooling step needed
    to get Node/git/Claude Code CLI into the image sandbox containers boot from).
    """
    rc, out, _ = await _run("docker", "image", "inspect", settings.SANDBOX_IMAGE)
    if rc == 0:
        return

    build_context = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sandbox-image")
    logger.info(f"Building sandbox image {settings.SANDBOX_IMAGE} from {build_context}")
    rc, _, err = await _run(
        "docker", "build", "-t", settings.SANDBOX_IMAGE, build_context,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to build sandbox image: {err}")
