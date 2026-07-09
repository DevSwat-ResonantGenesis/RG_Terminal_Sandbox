from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
import uuid

from .db import Base


class TerminalSession(Base):
    """One row per persistent terminal (per terminal_id), not per websocket
    connection/session. Modeled on RG_Auth's RefreshToken/session-record
    pattern (id, user_id, org_id, created_at/expires-style timestamps).
    """
    __tablename__ = "terminal_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    terminal_id = Column(String(128), unique=True, index=True, nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    org_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    project_id = Column(String(128), nullable=True, index=True)

    container_id = Column(String(128), nullable=True)
    container_name = Column(String(128), nullable=True)
    git_repo_url = Column(String(512), nullable=True)

    # creating | running | idle | stopped | error
    status = Column(String(20), default="creating", nullable=False, index=True)

    cpu_limit = Column(String(16), nullable=True)
    memory_limit_mb = Column(Integer, nullable=True)
    idle_timeout_seconds = Column(Integer, nullable=True)

    transcript_storage_key = Column(String(512), nullable=True)
    transcript_storage_bucket = Column(String(128), nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_active_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
