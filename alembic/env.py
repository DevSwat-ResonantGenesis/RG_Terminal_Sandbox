import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.db import Base
from app import models  # noqa: F401 - ensures models are registered

config = context.config

db_user = os.getenv("TERMINAL_SANDBOX_DB_USER", "terminal_sandbox_user")
db_password = os.getenv("TERMINAL_SANDBOX_DB_PASSWORD", "terminal_sandbox_pass")
db_host = os.getenv("TERMINAL_SANDBOX_DB_HOST", "terminal_sandbox_db")
db_port = os.getenv("TERMINAL_SANDBOX_DB_PORT", "5432")
db_name = os.getenv("TERMINAL_SANDBOX_DB_NAME", "terminal_sandbox_db")

database_url = os.getenv("TERMINAL_SANDBOX_DATABASE_URL") or (
    f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
)
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
