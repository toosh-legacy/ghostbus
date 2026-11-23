"""Application settings. Environment variables only (documented in .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    mode: str = "lite"  # lite | full
    lake_dir: Path = REPO_ROOT / "lake"
    var_dir: Path = REPO_ROOT / "var"
    config_dir: Path = REPO_ROOT / "config"
    database_url: str = ""  # empty => sqlite var/app.db
    redis_url: str = "redis://localhost:6379/0"
    ground_truth_source: str = "real"  # real | synthetic
    recorder_interval_s: float = 20.0
    recorder_user_agent: str = (
        "GhostBus/0.1 (open-source transit reliability research project; polite polling 20s)"
    )
    admin_token: str = "dev-admin-token"
    copilot_provider: str = "offline"  # offline | ollama | azure_openai
    stream_bus: str = "memory"  # memory | redis | eventhubs
    live_state: str = "memory"  # memory | redis
    lake_store: str = "local"  # local | azurite | adls

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.var_dir / 'app.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
