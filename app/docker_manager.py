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


def ssh_identity_volume_for(user_id: str) -> str:
    """One persistent volume per user (not per terminal_id/project) so the
    same registered SSH target works from any of that user's terminal
    sessions without re-pasting a new public key each time.
    """
    h = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return f"sshid_{h}_identity"


def egress_proxy_name_for(terminal_id: str) -> str:
    h = hashlib.sha256(terminal_id.encode()).hexdigest()[:16]
    return f"term_{h}_egress"


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


async def create_container(
    terminal_id: str,
    user_id: str,
    anthropic_key: Optional[str] = None,
    egress_proxy_url: Optional[str] = None,
    mount_ssh_identity: bool = False,
    workspace_token: Optional[str] = None,
) -> Tuple[str, bool]:
    """Create the persistent sandbox container for a terminal_id. Idempotent:
    if a container with this terminal's name already exists, reuse it instead
    of creating a duplicate (this is what makes reconnects reuse state).
    `anthropic_key`, when provided, is only applied at creation time - it is
    not refreshed into an already-running container on reuse.

    `egress_proxy_url` overrides the default shared terminal_egress_proxy
    with a per-session sidecar (see create_egress_proxy) - only set when the
    user has an opt-in registered SSH host. `mount_ssh_identity` attaches
    that user's persistent SSH identity volume when true. `workspace_token`
    (an RGW- scoped token, see workspace_tokens.py) is injected as
    RG_WORKSPACE_TOKEN so Claude Code CLI can call the platform's own API -
    the caller mints one unconditionally before calling this function since
    create-vs-reuse isn't known until this call returns; it's simply unused
    on the reuse path below.

    Returns (container_id, created) - `created` is False on every reuse path
    (running or restarted), True only the first time this terminal_id's
    container is actually built. Callers use this to sync project files in
    exactly once, not on every reconnect.
    """
    existing = await find_container_id(terminal_id)
    if existing:
        # Make sure it's actually running, not just present-but-stopped.
        rc, out, _ = await _run("docker", "inspect", "-f", "{{.State.Running}}", existing)
        if rc == 0 and out.strip() == "true":
            return existing, False
        await _run("docker", "start", existing)
        return existing, False

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
        f"-e=HTTPS_PROXY={egress_proxy_url or settings.SANDBOX_EGRESS_PROXY_URL}",
        f"-e=HTTP_PROXY={egress_proxy_url or settings.SANDBOX_EGRESS_PROXY_URL}",
        "-e=NO_PROXY=localhost,127.0.0.1",
    ]
    if anthropic_key:
        docker_cmd.append(f"-e=ANTHROPIC_API_KEY={anthropic_key}")
    if workspace_token:
        docker_cmd.append(f"-e=RG_WORKSPACE_TOKEN={workspace_token}")
    if mount_ssh_identity:
        # Read-only: the container's own ~/.ssh (tmpfs, ephemeral) gets a
        # copy of the private key placed by _seed_ssh_identity after start -
        # never write the persistent identity volume directly from inside
        # an interactive shell.
        docker_cmd.append(f"-v={ssh_identity_volume_for(user_id)}:/mnt/ssh_identity:ro")

    docker_cmd += [settings.SANDBOX_IMAGE, "sleep", "infinity"]

    logger.info(f"Creating sandbox container for terminal_id={terminal_id}")
    rc, out, err = await _run(*docker_cmd)
    if rc != 0:
        raise RuntimeError(f"Failed to create sandbox container: {err}")

    container_id = out.strip()
    if mount_ssh_identity:
        await _seed_ssh_identity(container_id)

    return container_id, True


async def _seed_ssh_identity(container_id: str) -> None:
    """Copy the user's persistent SSH private key from the read-only
    /mnt/ssh_identity mount into ~/.ssh inside the container's ephemeral
    (tmpfs) home, with the permissions ssh requires (0600, dir 0700), and
    write a minimal ssh config disabling interactive host-key prompts (the
    shell has no TTY to answer "yes" to on first connect).
    """
    rc, _, _ = await _run(
        "docker", "exec", container_id, "test", "-f", "/mnt/ssh_identity/id_ed25519",
    )
    if rc != 0:
        return  # no keypair generated yet for this user

    await _run("docker", "exec", "-u", "root", container_id, "mkdir", "-p", "/home/sandboxuser/.ssh")
    await _run(
        "docker", "exec", "-u", "root", container_id, "sh", "-c",
        "cp /mnt/ssh_identity/id_ed25519 /home/sandboxuser/.ssh/id_ed25519 && "
        "cp /mnt/ssh_identity/id_ed25519.pub /home/sandboxuser/.ssh/id_ed25519.pub && "
        "printf 'StrictHostKeyChecking accept-new\\n' > /home/sandboxuser/.ssh/config && "
        f"chown -R {settings.SANDBOX_UID}:{settings.SANDBOX_UID} /home/sandboxuser/.ssh && "
        "chmod 700 /home/sandboxuser/.ssh && "
        "chmod 600 /home/sandboxuser/.ssh/id_ed25519 /home/sandboxuser/.ssh/config",
    )


async def ensure_ssh_keypair(user_id: str) -> Optional[str]:
    """Generate an ed25519 keypair for this user if one doesn't already
    exist in their persistent identity volume, and return the public key
    text either way. The private key never leaves this volume - it's only
    ever mounted read-only into that same user's own terminal containers
    (see mount_ssh_identity above). Runs in a throwaway, network-isolated
    helper container so this works even with no terminal session open.
    """
    volume = ssh_identity_volume_for(user_id)
    helper_name = f"{volume}_keygen"

    await _run(
        "docker", "run", "--rm",
        f"--name={helper_name}",
        "--network=none",
        "--security-opt=no-new-privileges",
        "--cap-drop=ALL",
        f"-v={volume}:/identity",
        settings.SANDBOX_IMAGE, "sh", "-c",
        "test -f /identity/id_ed25519 || "
        "ssh-keygen -t ed25519 -f /identity/id_ed25519 -N '' -C rg-terminal-sandbox >/dev/null 2>&1",
    )

    rc, out, err = await _run(
        "docker", "run", "--rm", "--network=none",
        f"-v={volume}:/identity:ro",
        settings.SANDBOX_IMAGE, "cat", "/identity/id_ed25519.pub",
    )
    if rc != 0 or not out.strip():
        logger.warning(f"ensure_ssh_keypair failed for user_id={user_id}: {err}")
        return None
    return out.strip()


async def copy_files_into_container(container_id: str, files: list) -> None:
    """Write [{file_path, content}, ...] into /workspace inside the
    container via `docker cp`, called once right after container creation
    to seed it with the user's existing IDE project files. Best-effort per
    file - one bad path shouldn't abort the whole sync.
    """
    import tempfile

    for f in files:
        file_path = (f.get("file_path") or "").lstrip("/")
        if not file_path or ".." in file_path.split("/"):
            continue
        content = f.get("content", "")
        with tempfile.TemporaryDirectory() as tmp:
            local_path = os.path.join(tmp, os.path.basename(file_path))
            with open(local_path, "w", encoding="utf-8", errors="replace") as fh:
                fh.write(content)
            dest = f"{container_id}:/workspace/{file_path}"
            # Ensure parent dirs exist inside the container before copying in.
            parent = os.path.dirname(file_path)
            if parent:
                await _run("docker", "exec", "-u", "root", container_id, "mkdir", "-p", f"/workspace/{parent}")
                await _run(
                    "docker", "exec", "-u", "root", container_id,
                    "chown", "-R", str(settings.SANDBOX_UID), f"/workspace/{parent.split('/')[0]}",
                )
            rc, _, err = await _run("docker", "cp", local_path, dest)
            if rc != 0:
                logger.warning(f"copy_files_into_container: failed to copy {file_path}: {err}")
                continue
            # docker cp writes as root regardless of the container's --user;
            # the interactive shell runs as SANDBOX_UID (non-root), so hand
            # ownership over or it can't read/edit what it was just given.
            await _run(
                "docker", "exec", "-u", "root", container_id,
                "chown", str(settings.SANDBOX_UID), f"/workspace/{file_path}",
            )


_SYNC_MARKER = "/workspace/.rg_last_sync"


async def workspace_changed_since_last_sync(container_id: str) -> bool:
    """Cheap check for the periodic sync loop: has anything under
    /workspace been written since the last sync? Avoids doing a full
    docker cp (copy_workspace_out) on every tick for containers with no
    new writes. Uses a marker file's mtime rather than a network call -
    the sandbox container has no path back to this service to report
    changes itself (terminal_egress_net has no route to app-network).
    """
    rc, _, _ = await _run("docker", "exec", container_id, "test", "-f", _SYNC_MARKER)
    if rc != 0:
        # No marker yet - either brand new or never synced. Only treat as
        # "changed" if there's at least one real file to sync.
        rc2, out, _ = await _run("docker", "exec", container_id, "find", "/workspace", "-type", "f")
        return rc2 == 0 and bool(out.strip())

    rc, out, _ = await _run(
        "docker", "exec", container_id, "find", "/workspace", "-type", "f", "-newer", _SYNC_MARKER,
    )
    return rc == 0 and bool(out.strip())


async def mark_workspace_synced(container_id: str) -> None:
    await _run("docker", "exec", "-u", "root", container_id, "sh", "-c", f"touch {_SYNC_MARKER}")


async def copy_workspace_out(container_id: str) -> list:
    """Read every regular file under /workspace back out via `docker cp`,
    called before a terminal session's container is torn down so anything
    written by Claude Code CLI (or the user) persists back to Gateway.
    Returns [{file_path, content}, ...]; skips files that fail to decode
    as UTF-8 text (binary artifacts, node_modules, etc. aren't meant to
    round-trip through Hash Sphere).
    """
    import tempfile

    rc, out, _ = await _run(
        "docker", "exec", container_id, "find", "/workspace", "-type", "f",
    )
    if rc != 0:
        return []
    paths = [p for p in out.strip().splitlines() if p and p != _SYNC_MARKER]

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for container_path in paths:
            rel_path = container_path[len("/workspace/"):] if container_path.startswith("/workspace/") else container_path
            local_path = os.path.join(tmp, os.path.basename(container_path))
            rc, _, err = await _run("docker", "cp", f"{container_id}:{container_path}", local_path)
            if rc != 0:
                logger.warning(f"copy_workspace_out: failed to copy {container_path}: {err}")
                continue
            try:
                with open(local_path, "r", encoding="utf-8") as fh:
                    content = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            finally:
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            results.append({"file_path": rel_path, "content": content})

    return results


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


async def ensure_egress_proxy_image_built() -> None:
    """Build the per-session egress-sidecar image on startup if missing -
    same self-contained-build pattern as ensure_sandbox_image_built."""
    rc, _, _ = await _run("docker", "image", "inspect", settings.EGRESS_PROXY_IMAGE)
    if rc == 0:
        return

    build_context = os.path.join(os.path.dirname(os.path.dirname(__file__)), "egress-proxy-image")
    logger.info(f"Building egress proxy image {settings.EGRESS_PROXY_IMAGE} from {build_context}")
    rc, _, err = await _run("docker", "build", "-t", settings.EGRESS_PROXY_IMAGE, build_context)
    if rc != 0:
        raise RuntimeError(f"Failed to build egress proxy image: {err}")


def _render_egress_squid_conf(extra_host: str, extra_port: int) -> str:
    """Same base allowlist as RG_core/squid/squid.conf, plus one narrow
    exception for the user's registered host:port. `extra_host` may be a
    hostname (matched via dstdomain) or a raw IP (matched via dst) - never
    a range, and never added to the shared proxy's own config.
    """
    import ipaddress

    try:
        ipaddress.ip_address(extra_host)
        host_acl = f"acl allowed_ssh_dst dst {extra_host}/32"
    except ValueError:
        host_acl = f"acl allowed_ssh_dst dstdomain {extra_host}"

    return f"""http_port 3128
visible_hostname term_egress_sidecar

acl allowed_ssl_ports port 443
acl allowed_ssl_ports port {extra_port}
acl CONNECT method CONNECT

acl allowed_dst_domains dstdomain .anthropic.com
acl allowed_dst_domains dstdomain .claude.com
acl allowed_dst_domains dstdomain github.com
acl allowed_dst_domains dstdomain api.github.com
acl allowed_dst_domains dstdomain codeload.github.com
acl allowed_dst_domains dstdomain .dev-swat.com

{host_acl}
acl allowed_ssh_port port {extra_port}

acl private_dst dst 10.0.0.0/8
acl private_dst dst 172.16.0.0/12
acl private_dst dst 192.168.0.0/16
acl private_dst dst 127.0.0.0/8
acl private_dst dst 169.254.0.0/16

# Single scoped exception to private_dst below - exactly this user's one
# registered host, never the whole private range.
http_access allow CONNECT allowed_ssh_dst allowed_ssh_port
http_access deny private_dst
http_access deny CONNECT !allowed_ssl_ports
http_access allow CONNECT allowed_dst_domains
http_access deny CONNECT
http_access deny all
"""


async def create_egress_proxy(terminal_id: str, host: str, port: int) -> str:
    """Launch a per-session Squid sidecar for this terminal, allowlisting the
    same base anthropic/github domains as the shared terminal_egress_proxy
    plus one exception for `host:port`. Only called when the terminal's
    owner has an opt-in registered SSH host - everyone else keeps using the
    shared proxy untouched. Returns the proxy URL to pass as HTTP(S)_PROXY.
    """
    await ensure_egress_proxy_image_built()

    name = egress_proxy_name_for(terminal_id)
    rc, out, _ = await _run("docker", "ps", "-aq", "--filter", f"name=^{name}$")
    if rc == 0 and out.strip():
        # Already running from a previous connect on this terminal_id.
        return f"http://{name}:3128"

    rc, out, err = await _run(
        "docker", "create",
        f"--name={name}",
        "--security-opt=no-new-privileges",
        "--cap-drop=ALL",
        f"--network={settings.SANDBOX_NETWORK}",
        settings.EGRESS_PROXY_IMAGE,
    )
    if rc != 0:
        raise RuntimeError(f"Failed to create egress proxy sidecar: {err}")
    container_id = out.strip()

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        conf_path = os.path.join(tmp, "squid.conf")
        with open(conf_path, "w") as fh:
            fh.write(_render_egress_squid_conf(host, port))
        # The sidecar's filesystem is separate from the real Docker host's,
        # so this must go through `docker cp` (API-level copy) rather than a
        # bind mount, same reasoning as copy_files_into_container.
        rc, _, err = await _run("docker", "cp", conf_path, f"{container_id}:/etc/squid/squid.conf")
        if rc != 0:
            await _run("docker", "rm", "-f", container_id)
            raise RuntimeError(f"Failed to write egress proxy config: {err}")

    # Second network leg for real outbound connectivity, mirroring how
    # terminal_egress_proxy itself is dual-homed.
    await _run("docker", "network", "connect", settings.APP_NETWORK, container_id)
    rc, _, err = await _run("docker", "start", container_id)
    if rc != 0:
        raise RuntimeError(f"Failed to start egress proxy sidecar: {err}")

    logger.info(f"Created per-session egress proxy {name} for terminal_id={terminal_id} -> {host}:{port}")
    return f"http://{name}:3128"


async def remove_egress_proxy(terminal_id: str) -> None:
    name = egress_proxy_name_for(terminal_id)
    await _run("docker", "rm", "-f", name)
