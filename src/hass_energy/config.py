from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hass_energy.models.loads import LoadConfig
from hass_energy.models.plant import PlantConfig

logger = logging.getLogger(__name__)


class HomeAssistantConfig(BaseModel):
    base_url: str = ""
    token: str | None = None
    verify_tls: bool = True

    model_config = ConfigDict(extra="forbid")


class EnergySystemConfig(BaseModel):
    forecast_window_hours: int = 24
    poll_interval_seconds: int = 300

    model_config = ConfigDict(extra="forbid")


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / "data")

    model_config = ConfigDict(extra="forbid")


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    homeassistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)
    energy: EnergySystemConfig = Field(default_factory=EnergySystemConfig)
    plant: PlantConfig | None = None
    loads: list[LoadConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


def load_app_config(config_path: Path | None) -> AppConfig:
    if config_path is None:
        logger.info("No config path provided; using defaults")
        config_path = Path("config.yaml")

    if not config_path.exists():
        raise ValueError(f"Config file {config_path} not found")

    try:
        loaded: Any = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Config file is not valid YAML: {config_path}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("Top-level config must be a mapping")

    try:
        config = AppConfig.model_validate(cast(dict[str, Any], loaded))
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration in {config_path}: {exc}") from exc

    _ensure_data_dir(config.server.data_dir)
    return config


def _ensure_data_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
