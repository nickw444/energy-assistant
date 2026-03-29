from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from energy_assistant.lib.home_assistant import HomeAssistantConfig
from energy_assistant.models.inputs import (
    ForecastInputConfig,
    InputConfig,
    InputValueKind,
    ScalarInputConfig,
    input_value_kind,
)
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    ControlledEvComponentConfig,
    GridComponentConfig,
    InputReference,
    InverterComponentConfig,
    LoadComponentConfig,
    PlantComponentConfig,
    PvComponentConfig,
    SwitchboardComponentConfig,
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
        switchboards = {
            key: component
            for key, component in self.plant.items()
            if isinstance(component, SwitchboardComponentConfig)
        }
        grids = {
            key: component
            for key, component in self.plant.items()
            if isinstance(component, GridComponentConfig)
        }
        loads = {
            key: component
            for key, component in self.plant.items()
            if isinstance(component, LoadComponentConfig)
        }
        inverters = {
            key: component
            for key, component in self.plant.items()
            if isinstance(component, InverterComponentConfig)
        }

        if len(switchboards) != 1:
            raise ValueError("plant must define exactly one switchboard component")
        if len(grids) != 1:
            raise ValueError("plant must define exactly one grid component")
        if len(loads) != 1:
            raise ValueError("plant must define exactly one load component")

        batteries_by_inverter: set[str] = set()
        pv_by_inverter: set[str] = set()

        for key, component in self.plant.items():
            if isinstance(component, GridComponentConfig):
                self._expect_connection_target(
                    key,
                    component.connection,
                    SwitchboardComponentConfig,
                )
                self._expect_input(
                    component.price_import.source,
                    ForecastInputConfig,
                    InputValueKind.PRICE,
                )
                self._expect_input(
                    component.price_export.source,
                    ForecastInputConfig,
                    InputValueKind.PRICE,
                )
                if component.realtime_grid_power is not None:
                    self._expect_input(
                        component.realtime_grid_power,
                        ScalarInputConfig,
                        InputValueKind.POWER,
                    )
                continue

            if isinstance(component, LoadComponentConfig):
                self._expect_connection_target(
                    key,
                    component.connection,
                    SwitchboardComponentConfig,
                )
                self._expect_input(component.power, ForecastInputConfig, InputValueKind.POWER)
                continue

            if isinstance(component, InverterComponentConfig):
                self._expect_connection_target(
                    key,
                    component.connection,
                    SwitchboardComponentConfig,
                )
                continue

            if isinstance(component, BatteryComponentConfig):
                self._expect_connection_target(
                    key,
                    component.connection,
                    InverterComponentConfig,
                )
                batteries_by_inverter.add(component.connection)
                self._expect_input(
                    component.state_of_charge_pct,
                    ScalarInputConfig,
                    InputValueKind.PERCENTAGE,
                )
                self._expect_input(
                    component.realtime_power,
                    ScalarInputConfig,
                    InputValueKind.POWER,
                )
                continue

            if isinstance(component, PvComponentConfig):
                self._expect_connection_target(
                    key,
                    component.connection,
                    InverterComponentConfig,
                )
                pv_by_inverter.add(component.connection)
                self._expect_input(component.forecast, ForecastInputConfig, InputValueKind.POWER)
                continue

            if isinstance(component, ControlledEvComponentConfig):
                self._expect_connection_target(
                    key,
                    component.connection,
                    SwitchboardComponentConfig,
                )
                self._expect_input(component.connected, ScalarInputConfig, InputValueKind.BOOLEAN)
                if component.can_connect is not None:
                    self._expect_input(
                        component.can_connect,
                        ScalarInputConfig,
                        InputValueKind.BOOLEAN,
                    )
                self._expect_input(
                    component.realtime_power,
                    ScalarInputConfig,
                    InputValueKind.POWER,
                )
                self._expect_input(
                    component.state_of_charge_pct,
                    ScalarInputConfig,
                    InputValueKind.PERCENTAGE,
                )
                continue

        if not inverters and (batteries_by_inverter or pv_by_inverter):
            raise ValueError("battery/pv components require an inverter component")
        return self

    def _expect_connection_target(
        self,
        component_key: str,
        target_key: str,
        expected_type: type[PlantComponentConfig],
    ) -> None:
        if component_key == target_key:
            raise ValueError(f"component {component_key} cannot connect to itself")
        target = self.plant.get(target_key)
        if target is None:
            raise ValueError(
                "component "
                f"{component_key} references missing connection target {target_key}"
            )
        if not isinstance(target, expected_type):
            expected_name = expected_type.__name__.removesuffix("Config")
            raise ValueError(
                f"component {component_key} must connect to a {expected_name}; "
                f"got {type(target).__name__}"
            )

    def _expect_input(
        self,
        reference: InputReference,
        expected_input_type: type[ScalarInputConfig] | type[ForecastInputConfig],
        expected_value_kind: InputValueKind,
    ) -> None:
        input_config = self.inputs.get(reference.key)
        if input_config is None:
            raise ValueError(f"missing input reference: {reference.key}")
        if not isinstance(input_config, expected_input_type):
            expected_name = "scalar" if expected_input_type is ScalarInputConfig else "forecast"
            raise ValueError(
                f"input {reference.key} must be a {expected_name} input"
            )
        actual_kind = input_value_kind(input_config)
        if actual_kind is not expected_value_kind:
            raise ValueError(
                f"input {reference.key} must have value kind {expected_value_kind.value}; "
                f"got {actual_kind.value}"
            )
