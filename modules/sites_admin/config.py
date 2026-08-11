import os
from dataclasses import dataclass


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-change-me")
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me")
    # Postgres connection string — Render's full Internal Database URL, e.g.
    # postgresql://user:password@host/dbname. This is the ONLY datastore; there
    # is no SQLite/persistent-disk fallback.
    database_url: str = os.getenv("DATABASE_URL", "")
    mock_mode: bool = env_bool("MOCK_MODE", True)
    enable_write_actions: bool = env_bool("ENABLE_WRITE_ACTIONS", False)
    use_bg_as_platform_cost: bool = env_bool("USE_BG_AS_PLATFORM_COST", False)
    api_base_url: str = os.getenv(
        "SIMVOLY_API_BASE_URL", "https://api.smart1sites.com"
    ).rstrip("/")
    api_key: str = os.getenv("SIMVOLY_API_KEY", "")
    timeout_seconds: int = int(os.getenv("SIMVOLY_TIMEOUT_SECONDS", "30"))
    verify_ssl: bool = env_bool("SIMVOLY_VERIFY_SSL", True)
    reseller_name: str = os.getenv("RESELLER_NAME", "Smart 1 Sites")


SETTINGS = Settings()
