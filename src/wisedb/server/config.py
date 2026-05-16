"""Параметры приложения (переменные окружения WISEDB_*)."""

from __future__ import annotations

from typing import Any, Optional

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WISEDB_", extra="ignore")

    api_key: Optional[str] = Field(
        default=None,
        description="Админ-ключ для /v1/databases/* (создание/список/удаление).",
    )
    data_dir: Optional[str] = Field(
        default=None,
        description="Корень данных: catalog.sqlite и databases/{id}/; по умолчанию родитель sqlite_path или /app/data.",
    )
    bootstrap_default_db: bool = Field(
        default=True,
        description="Если каталог пуст — при старте создать базу 'default'.",
    )
    default_db_password: str = Field(
        default="",
        description="Пароль для создаваемой при старте базы 'default' (пусто — без пароля).",
    )
    embedding_model: Optional[str] = None
    log_level: str = "INFO"
    hnsw_max_elements: int = 2_000_000
    hnsw_ef: int = 64
    hnsw_m: int = 32
    sqlite_path: Optional[str] = Field(default=None)
    library_path: Optional[str] = Field(
        default=None,
        description="Гибрид: чанки и вложения на диске; SQLite хранит в основном рёбра.",
    )
    sleep_reinforce_on_retrieve: bool = Field(
        default=False,
        description="При true retrieve помечает wisedb_last_reinforced_at на внутренних рёбрах из выдачи.",
    )
    sleep_llm_api_key: Optional[str] = Field(
        default=None,
        repr=False,
        description="Опциональный ключ OpenAI-совместимого API для фазы sleep llm_optimize.",
    )
    sleep_llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Базовый URL OpenAI-совместимого API для llm_optimize.",
    )
    sleep_llm_model: str = Field(
        default="gpt-4o-mini",
        description="Имя модели для llm_optimize.",
    )

    def resolved_data_dir(self) -> str:
        if self.data_dir:
            return self.data_dir
        if self.sqlite_path:
            return str(Path(self.sqlite_path).parent)
        return "/app/data"

    @field_validator("library_path", mode="before")
    @classmethod
    def _library_empty(cls, v: Any) -> Optional[str]:
        if v is None or v == "":
            return None
        return str(v)

    @field_validator("sqlite_path", mode="before")
    @classmethod
    def _sqlite_empty_off(cls, v: Any) -> Optional[str]:
        if v is None or v == "":
            return None
        return str(v)

    @field_validator("sleep_llm_api_key", mode="before")
    @classmethod
    def _sleep_llm_api_key_empty(cls, v: Any) -> Optional[str]:
        if v is None or v == "":
            return None
        return str(v)


def get_settings() -> Settings:
    return Settings()
