import os
import secrets
from pydantic_settings import BaseSettings


def _get_required_secret(env_var: str, default_dev: str) -> str:
    """Get a secret from environment, fail in production if not set."""
    value = os.getenv(env_var)
    if value:
        return value

    env = os.getenv("TERMINAL_SANDBOX_ENVIRONMENT", "development")
    if env == "production":
        raise ValueError(f"CRITICAL: {env_var} must be set in production environment!")

    print(f"[WARNING] Using default {env_var} - set via environment for production!")
    return default_dev


class Settings(BaseSettings):
    SERVICE_NAME: str = "terminal-sandbox"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"  # development | staging | production

    DB_HOST: str = "terminal_sandbox_db"
    DB_PORT: int = 5432
    DB_USER: str = "terminal_sandbox_user"
    DB_PASSWORD: str = "terminal_sandbox_pass"
    DB_NAME: str = "terminal_sandbox_db"

    # SECURITY: must be set via environment in production
    INTERNAL_SERVICE_KEY: str = ""

    AUTH_SERVICE_URL: str = os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000")
    STORAGE_SERVICE_URL: str = os.getenv("STORAGE_SERVICE_URL", "http://storage_service:8000")
    BILLING_SERVICE_URL: str = os.getenv("BILLING_SERVICE_URL", "http://billing_service:8000")

    # Sandbox container defaults - larger than the short-lived snippet-exec
    # baseline (RG_Code_Execution's CodeExecutor uses 256m/0.5cpu) because this
    # runs a persistent dev-grade session (editor loop + Claude Code + npm)
    # rather than one short script.
    SANDBOX_IMAGE: str = os.getenv("TERMINAL_SANDBOX_IMAGE", "rg-terminal-sandbox-image:latest")
    SANDBOX_MEMORY_MB: int = int(os.getenv("TERMINAL_SANDBOX_MEMORY_MB", "1024"))
    SANDBOX_CPUS: str = os.getenv("TERMINAL_SANDBOX_CPUS", "1.0")
    SANDBOX_PIDS_LIMIT: int = int(os.getenv("TERMINAL_SANDBOX_PIDS_LIMIT", "256"))
    SANDBOX_TMPFS_SIZE_MB: int = int(os.getenv("TERMINAL_SANDBOX_TMPFS_SIZE_MB", "256"))
    SANDBOX_UID: int = int(os.getenv("TERMINAL_SANDBOX_UID", "10001"))

    # Phase 3: egress-restricted network. Containers get no route to the open
    # internet or to app-network except through terminal_egress_proxy (squid),
    # which only allows CONNECT to api.anthropic.com/github.com/api.github.com/
    # codeload.github.com. Never point this at app-network or a non-internal
    # network without that proxy allowlist in place.
    SANDBOX_NETWORK: str = os.getenv("TERMINAL_SANDBOX_NETWORK", "terminal_egress_net")
    SANDBOX_EGRESS_PROXY_URL: str = os.getenv(
        "TERMINAL_SANDBOX_EGRESS_PROXY_URL", "http://terminal_egress_proxy:3128"
    )

    IDLE_TIMEOUT_SECONDS: int = int(os.getenv("TERMINAL_SANDBOX_IDLE_TIMEOUT_SECONDS", "3600"))
    REAPER_INTERVAL_SECONDS: int = int(os.getenv("TERMINAL_SANDBOX_REAPER_INTERVAL_SECONDS", "300"))

    class Config:
        env_prefix = "TERMINAL_SANDBOX_"
        case_sensitive = False


settings = Settings()

if not settings.INTERNAL_SERVICE_KEY:
    settings.INTERNAL_SERVICE_KEY = _get_required_secret(
        "TERMINAL_SANDBOX_INTERNAL_SERVICE_KEY",
        "internal-service-key-" + secrets.token_hex(8),
    )


def get_database_url() -> str:
    db_url = os.getenv("TERMINAL_SANDBOX_DATABASE_URL") or os.getenv("DATABASE_URL")
    if db_url:
        return db_url.replace("postgresql://", "postgresql+asyncpg://").replace("?sslmode=", "?ssl=")

    return (
        f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )
