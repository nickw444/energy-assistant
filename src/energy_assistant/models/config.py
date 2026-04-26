from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from energy_assistant.lib.home_assistant import HomeAssistantConfig
from energy_assistant.models.inputs import (
    InputConfig,
    ScalarInputConfig,
    input_value_kind,
)
from energy_assistant.models.plant import (
    InputRequirement,
    PlantComponentConfig,
    normalize_registry_key,
)


class EmsConfig(BaseModel):
    timestep_minutes: int = Field(default=5, ge=1, le=1440)
    horizon_minutes: int = Field(default=120, ge=1, le=525600)
    high_res_timestep_minutes: int | None = Field(default=None, ge=1, le=1440)
    high_res_horizon_minutes: int | None = Field(default=None, ge=1, le=525600)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_interval_settings(self) -> EmsConfig:
        if self.high_res_timestep_minutes is None and self.high_res_horizon_minutes is None:
            return self
        if self.high_res_timestep_minutes is None or self.high_res_horizon_minutes is None:
            raise ValueError(
                "high_res_timestep_minutes and high_res_horizon_minutes must be set together"
            )
        if self.high_res_timestep_minutes > self.timestep_minutes:
            raise ValueError("high_res_timestep_minutes must be <= timestep_minutes")
        if self.high_res_horizon_minutes % self.high_res_timestep_minutes != 0:
            raise ValueError(
                "high_res_horizon_minutes must be a multiple of high_res_timestep_minutes"
            )
        if self.high_res_horizon_minutes > self.horizon_minutes:
            raise ValueError("high_res_horizon_minutes must be <= horizon_minutes")
        return self


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 6070
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / "data")

    model_config = ConfigDict(extra="forbid")


class AppConfig(BaseModel):
    """Top-level app configuration."""

    server: ServerConfig = Field(default_factory=ServerConfig)
    homeassistant: HomeAssistantConfig
    ems: EmsConfig = Field(default_factory=EmsConfig)
    inputs: dict[str, InputConfig]
    plant: dict[str, PlantComponentConfig]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _normalize_registry_keys(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(cast(dict[str, Any], data))
        for field in ("inputs", "plant"):
            raw = payload.get(field)
            if raw is None:
                continue
            if not isinstance(raw, dict):
                raise ValueError(f"{field} must be a mapping")
            normalized: dict[str, object] = {}
            for key, value in cast(dict[object, object], raw).items():
                if not isinstance(key, str):
                    raise ValueError(f"{field} keys must be strings")
                normalized_key = normalize_registry_key(key)
                if normalized_key in normalized:
                    raise ValueError(f"duplicate {field} key after normalization: {normalized_key}")
                normalized[normalized_key] = value
            payload[field] = normalized
        return payload

    @model_validator(mode="after")
    def _validate_ems_schema(self) -> AppConfig:
        for component in self.plant.values():
            for requirement in component.input_requirements():
                self._expect_input(requirement)
        return self

    def _expect_input(self, requirement: InputRequirement) -> None:
        input_config = self.inputs.get(requirement.reference.key)
        if input_config is None:
            raise ValueError(f"missing input reference: {requirement.reference.key}")
        if not isinstance(input_config, requirement.input_config_type):
            expected_name = (
                "scalar" if requirement.input_config_type is ScalarInputConfig else "forecast"
            )
            raise ValueError(
                f"input {requirement.reference.key} must be a {expected_name} input"
            )
        actual_kind = input_value_kind(input_config)
        if actual_kind is not requirement.value_kind:
            raise ValueError(
                f"input {requirement.reference.key} must have value kind "
                f"{requirement.value_kind.value}; "
                f"got {actual_kind.value}"
            )
