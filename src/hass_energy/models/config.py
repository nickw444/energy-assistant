from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hass_energy.lib.home_assistant import HomeAssistantConfig
from hass_energy.models.loads import LoadConfig
from hass_energy.models.plant import PlantConfig


class EmsConfig(BaseModel):
    timestep_minutes: int = Field(default=5, ge=1, le=1440)
    min_horizon_minutes: int = Field(default=120, ge=1, le=525600)
    high_res_timestep_minutes: int | None = Field(default=None, ge=1, le=1440)
    high_res_horizon_minutes: int | None = Field(default=None, ge=1, le=525600)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_interval_settings(self) -> EmsConfig:
        if (
            self.high_res_timestep_minutes is None
            and self.high_res_horizon_minutes is None
        ):
            return self
        if (
            self.high_res_timestep_minutes is None
            or self.high_res_horizon_minutes is None
        ):
            raise ValueError(
                "high_res_timestep_minutes and high_res_horizon_minutes must be set together"
            )
        if (
            self.high_res_horizon_minutes is not None
            and self.high_res_horizon_minutes % self.high_res_timestep_minutes != 0
        ):
            raise ValueError(
                "high_res_horizon_minutes must be a multiple of high_res_timestep_minutes"
            )
        return self


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

    @model_validator(mode="after")
    def _validate_load_ids_unique(self) -> AppConfig:
        ids = [load.id for load in self.loads]
        if len(ids) != len(set(ids)):
            raise ValueError("load ids must be unique")
        return self
