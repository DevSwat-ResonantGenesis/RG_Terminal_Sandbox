"""Client for RG_Auth's /auth/internal/workspace-tokens/mint - mints the
RGW- token injected into a workspace's container as RG_WORKSPACE_TOKEN so
Claude Code CLI can call the platform's own API (Agent OS, Builder) on the
owning user's behalf. See RG_Auth's WorkspaceAccessToken model.
"""
import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def mint_workspace_token(user_id: str, workspace_id: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.AUTH_SERVICE_URL}/auth/internal/workspace-tokens/mint",
                json={"user_id": user_id, "workspace_id": workspace_id},
                headers={"x-internal-service-key": settings.GATEWAY_INTERNAL_SERVICE_KEY},
            )
            if resp.status_code != 200:
                logger.warning(f"mint_workspace_token status={resp.status_code}: {resp.text[:200]}")
                return None
            return resp.json().get("token")
    except Exception as e:
        logger.warning(f"mint_workspace_token failed for workspace_id={workspace_id}: {e}")
        return None
