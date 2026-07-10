"""Client for RG_Gateway's /code/internal/project/* file API - lets this
service read/write a user's IDE project files (Memory Service/Hash Sphere)
directly, the same storage the browser IDE uses. Modeled on byok.py's
httpx client pattern.
"""
import logging
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def _headers(user_id: str) -> dict:
    return {
        "x-internal-service-key": settings.GATEWAY_INTERNAL_SERVICE_KEY,
        "x-user-id": user_id,
    }


async def fetch_project_files(project_id: str, user_id: str) -> list[dict]:
    """Returns [{file_path, content, language}, ...] for a project, or []
    on any failure - callers should treat a fetch failure as "nothing to
    sync in" rather than blocking terminal creation.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{settings.GATEWAY_URL}/api/v1/code/internal/project/files",
                params={"project_id": project_id},
                headers=_headers(user_id),
            )
            if resp.status_code != 200:
                logger.warning(f"fetch_project_files status={resp.status_code}: {resp.text[:200]}")
                return []
            return resp.json().get("files", [])
    except Exception as e:
        logger.warning(f"fetch_project_files failed for project_id={project_id}: {e}")
        return []


async def write_project_file(project_id: str, file_path: str, content: str, user_id: str) -> bool:
    """Persist one file back into Hash Sphere. Uses create-file, which is
    the same ingest-based persistence write() uses - safe to call for both
    genuinely new files and updates to existing ones.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.GATEWAY_URL}/api/v1/code/internal/project/create-file",
                json={"project_id": project_id, "file_path": file_path, "content": content},
                headers=_headers(user_id),
            )
            if resp.status_code != 200:
                logger.warning(f"write_project_file status={resp.status_code}: {resp.text[:200]}")
                return False
            return bool(resp.json().get("success"))
    except Exception as e:
        logger.warning(f"write_project_file failed for {file_path}: {e}")
        return False
