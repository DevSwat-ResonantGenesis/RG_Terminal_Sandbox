"""Client for RG_Auth's /auth/internal/user-ssh-hosts/* - the opt-in,
one-per-user SSH target a terminal container is allowed to reach through a
per-session egress sidecar (see docker_manager.create_egress_proxy).
"""
import hashlib
import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def get_registered_host(user_id: str) -> Optional[dict]:
    """Returns {"host", "port", "label", ...} or None if this user hasn't
    registered a host, or on any lookup failure (fail closed - no sidecar,
    no exception, same as today for everyone)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.AUTH_SERVICE_URL}/auth/internal/user-ssh-hosts/{user_id}",
                headers={"x-internal-service-key": settings.GATEWAY_INTERNAL_SERVICE_KEY},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data.get("host_entry") if data.get("registered") else None
    except Exception as e:
        logger.warning(f"get_registered_host failed for user_id={user_id}: {e}")
        return None


async def report_fingerprint(user_id: str, public_key: str) -> None:
    fingerprint = hashlib.sha256(public_key.encode()).hexdigest()[:32]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{settings.AUTH_SERVICE_URL}/auth/internal/user-ssh-hosts/{user_id}/fingerprint",
                json={"public_key_fingerprint": fingerprint},
                headers={"x-internal-service-key": settings.GATEWAY_INTERNAL_SERVICE_KEY},
            )
    except Exception as e:
        logger.warning(f"report_fingerprint failed for user_id={user_id}: {e}")
