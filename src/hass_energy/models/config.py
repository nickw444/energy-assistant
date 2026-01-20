from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hass_energy.lib.home_assistant import HomeAssistantConfig
from hass_energy.models.loads import LoadConfig
from hass_energy.models.plant import PlantConfig


class TerminalSocConfig(BaseModel):
    # Hard enforces end SoC >= start SoC; adaptive relaxes the target toward the
    # reserve SoC using a fixed 24h reference horizon.
    mode: Literal["hard", "adaptive"] = "adaptive"
    # Penalty applied per kWh of terminal SoC shortfall when adaptive slack is
    # used. Defaults to the median import price; set to "mean" for the average or
    # "median" for the P50 import price.
    penalty_per_kwh: float | Literal["mean", "median"] | None = Field(default="median")

    model_config = ConfigDict(extra="forbid")

    @field_validator("penalty_per_kwh")
    @classmethod
    def _validate_penalty_per_kwh(
        cls, value: float | Literal["mean", "median"] | None
    ) -> float | Literal["mean", "median"] | None:
        if value is None or value in {"mean", "median"}:
            return value
        if float(value) < 0:
            raise ValueError("penalty_per_kwh must be >= 0")
        return value


class EmsConfig(BaseModel):
    timestep_minutes: int = Field(default=5, ge=1, le=1440)
    min_horizon_minutes: int = Field(default=120, ge=1, le=525600)
    high_res_timestep_minutes: int | None = Field(default=None, ge=1, le=1440)
    high_res_horizon_minutes: int | None = Field(default=None, ge=1, le=525600)
    terminal_soc: TerminalSocConfig = Field(default_factory=TerminalSocConfig)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_interval_settings(self) -> EmsConfig:
        if self.high_res_timestep_minutes is None and self.high_res_horizon_minutes is None:
            return self
        if self.high_res_timestep_minutes is None or self.high_res_horizon_minutes is None:
            raise ValueError(
                "high_res_timestep_minutes and high_res_horizon_minutes must be set together"
            )
        if self.high_res_horizon_minutes % self.high_res_timestep_minutes != 0:
            raise ValueError(
                "high_res_horizon_minutes must be a multiple of high_res_timestep_minutes"
            )
        return self


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 6070
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
