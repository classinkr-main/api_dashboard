"""App settings, loaded from environment variables (.env supported).

Secrets never live in the repo: see .env.example for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASH_", env_file=".env", extra="ignore")

    # Deployment
    root_path: str = "/dash"
    host: str = "0.0.0.0"
    port: int = 8100
    secret_key: str = "change-me"  # cookie signing; must be overridden in production
    cookie_secure: bool = False  # set true behind HTTPS

    # Auth mode: "credential" = users type SID/secret at login,
    # "fixed" = SID/secret from env + shared access password.
    auth_mode: Literal["credential", "fixed"] = "credential"
    access_password: str = ""  # required in fixed mode
    session_ttl_hours: int = 12

    # ClassIn (used in fixed mode; credential mode gets these at login)
    classin_sid: str = ""
    classin_secret: str = ""
    classin_base_url: str = "https://api.eeo.cn"
    webhook_safekey: str = ""

    # Claude (schedule parsing, notification copy)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Storage
    data_dir: Path = Path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dashboard.db"

    @property
    def webhook_raw_dir(self) -> Path:
        return self.data_dir / "webhook"


@lru_cache
def get_settings() -> Settings:
    return Settings()
