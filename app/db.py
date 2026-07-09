import os
import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from .config import get_database_url

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


DB_POOL_CLASS = (os.getenv("TERMINAL_SANDBOX_DB_POOL_CLASS", "queue").strip().lower() or "queue")

_engine_kwargs = {"echo": False, "future": True, "pool_pre_ping": True}

if DB_POOL_CLASS in ("null", "none"):
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(
        {
            "pool_size": int(os.getenv("TERMINAL_SANDBOX_DB_POOL_SIZE", "5")),
            "max_overflow": int(os.getenv("TERMINAL_SANDBOX_DB_MAX_OVERFLOW", "5")),
            "pool_timeout": int(os.getenv("TERMINAL_SANDBOX_DB_POOL_TIMEOUT", "30")),
            "pool_recycle": int(os.getenv("TERMINAL_SANDBOX_DB_POOL_RECYCLE", "1800")),
        }
    )

engine = create_async_engine(get_database_url(), **_engine_kwargs)

SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def check_database_connection() -> bool:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connection successful")
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False
