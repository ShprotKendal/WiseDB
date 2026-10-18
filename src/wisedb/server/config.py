"""WiseDB HTTP server configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


SYSTEM_WIKI_DATABASE = "_wiki"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WISEDB_", extra="ignore")

    data_dir: Path = Path("./data")
    host: str = "127.0.0.1"
    port: int = 8000
    root_password: str = ""
    system_wiki: bool = True

    @property
    def catalog_dir(self) -> Path:
        return self.data_dir / "_catalog"

    def database_root(self, name: str) -> Path:
        return self.data_dir / "databases" / name


@lru_cache
def get_settings() -> Settings:
    return Settings()
