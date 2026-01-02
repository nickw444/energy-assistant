from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hass_energy.lib.home_assistant import HomeAssistantConfig
from hass_energy.models.loads import LoadConfig
from hass_energy.models.plant import PlantConfig


class EmsConfig(BaseModel):
    interval_duration: int = Field(default=5, ge=1, le=1440)
    num_intervals: int = Field(default=24, ge=1, le=10000)

    model_config = ConfigDict(extra="forbid")


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / "data")

    model_config = ConfigDict(extra="forbid")


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    homeassistant: HomeAssistantConfig
    ems: EmsConfig = Field(default_factory=EmsConfig)
    plant: PlantConfig
    loads: list[LoadConfig] = []

    model_config = ConfigDict(extra="forbid")
