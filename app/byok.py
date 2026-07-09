"""BYOK Anthropic key fetch for injecting into sandbox containers so `claude`
can authenticate. Adapted from RG_Axtention_IDE/app/llm_client.py's
fetch_user_byok_keys (same /api-keys/user/{user_id} internal endpoint).
"""
import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


async def fetch_anthropic_key(user_id: str) -> Optional[str]:
    if not user_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.AUTH_SERVICE_URL}/api-keys/user/{user_id}")
            if resp.status_code != 200:
                logger.warning(f"BYOK fetch status={resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            items = data.get("keys", []) if isinstance(data, dict) else data if isinstance(data, list) else []
            for item in items:
                provider = (item.get("provider") or "").lower()
                key = item.get("decrypted_key") or item.get("api_key") or item.get("key") or ""
                is_valid = item.get("is_valid", True)
                if provider == "anthropic" and key and is_valid:
                    return key
    except Exception as e:
        logger.warning(f"BYOK anthropic key lookup failed: {e}")
    return None
