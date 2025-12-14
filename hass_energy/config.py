from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class HomeAssistantConfig(BaseModel):
    base_url: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)
    verify_ssl: bool = True
    ws_max_size: int | None = Field(
        default=8_388_608,
        description="Maximum websocket frame size in bytes (None for unlimited).",
        ge=0,
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("home_assistant.base_url must be a non-empty string")
        return trimmed

    @field_validator("token")
    @classmethod
    def _resolve_token_field(cls, v: str) -> str:
        return _resolve_token(v.strip())


class Config(BaseModel):
    home_assistant: HomeAssistantConfig

    model_config = ConfigDict(extra="forbid")


def _resolve_token(raw_token: str) -> str:
    """Resolve a token, supporting env-prefixed values."""
    token_candidate = raw_token.strip()
    if token_candidate.startswith("env:"):
        env_var = token_candidate.split("env:", 1)[1].strip()
        if not env_var:
            raise ValueError("home_assistant.token uses env: prefix but no env var name provided")
        value = os.getenv(env_var)
        if value is None:
            raise ValueError(f"Environment variable {env_var} is not set for home_assistant.token")
        token_candidate = value.strip()
    if not token_candidate:
        raise ValueError("home_assistant.token must be a non-empty string")
    return token_candidate


def load_config(path: str | Path) -> Config:
    """Load configuration from a YAML file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("Config root must be a mapping")

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
