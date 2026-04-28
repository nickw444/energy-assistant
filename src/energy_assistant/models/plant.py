from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from energy_assistant.models.inputs import ForecastInputConfig, InputValueKind, ScalarInputConfig

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def normalize_registry_key(value: str) -> str:
    normalized = value.strip()
    if not _ID_PATTERN.match(normalized):
        raise ValueError("keys must be lowercase letters, numbers, and underscores")
    return normalized


class TimeWindow(BaseModel):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    months: list[str] | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("months", mode="before")
    @classmethod
    def _normalize_months(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("months must be a list of 3-letter month abbreviations")
        allowed = {abbr.lower() for abbr in calendar.month_abbr[1:]}
        items = cast(list[object], value)
        normalized: list[str] = []
        for item in items:
            if not isinstance(item, str):
                raise ValueError("months must be 3-letter month abbreviations (jan..dec)")
            month = item.strip().lower()
            if len(month) != 3 or month not in allowed:
                raise ValueError("months must be 3-letter month abbreviations (jan..dec)")
            normalized.append(month)
        return normalized

    @field_validator("months")
    @classmethod
    def _validate_months(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("months must not be empty")
        month_set = set(value)
        month_order = [abbr.lower() for abbr in calendar.month_abbr[1:]]
        return [abbr for abbr in month_order if abbr in month_set]


class InputReference(BaseModel):
    source: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_string(cls, value: object) -> object:
        if isinstance(value, str):
            return {"source": value}
        return value

    @field_validator("source")
    @classmethod
    def _normalize_source(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("inputs."):
            normalized = normalized[len("inputs.") :]
        return normalize_registry_key(normalized)

    @property
    def key(self) -> str:
        return self.source


@dataclass(frozen=True)
class InputRequirement:
    reference: InputReference
    input_config_type: type[ScalarInputConfig] | type[ForecastInputConfig]
    value_kind: InputValueKind


class PriceBiasFilterConfig(BaseModel):
    type: Literal["bias"]
    bias_pct: float = Field(default=0.0, ge=0, le=100)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PriceRiskFilterConfig(BaseModel):
    type: Literal["risk"]
    bias_pct: float = Field(default=0.0, ge=0, le=100)
    ramp_start_after_minutes: int = Field(default=30, ge=0)
    ramp_duration_minutes: int = Field(default=90, ge=0)
    curve: Literal["linear"] = "linear"
    import_price_floor: float | None = None
    export_price_ceiling: float | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_ramp_window(self) -> Self:
        if self.ramp_duration_minutes < 0:
            raise ValueError("ramp_duration_minutes must be >= 0")
        return self


PriceFilterConfig = Annotated[
    PriceBiasFilterConfig | PriceRiskFilterConfig,
    Field(discriminator="type"),
]


class PriceBindingConfig(BaseModel):
    source: InputReference
    filters: list[PriceBiasFilterConfig | PriceRiskFilterConfig] = []

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StoredEnergyValueConfig(BaseModel):
    source: InputReference
    statistic: Literal["median"] = "median"

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SwitchboardComponentConfig(BaseModel):
    type: Literal["switchboard"]
    name: str | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def input_requirements(self) -> tuple[InputRequirement, ...]:
        return ()


class GridConstraintsConfig(BaseModel):
    max_import_kw: float = Field(ge=0)
    max_export_kw: float = Field(ge=0)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GridComponentConfig(BaseModel):
    type: Literal["grid"]
    connection: str = Field(min_length=1)
    constraints: GridConstraintsConfig
    realtime_grid_power: InputReference | None = None
    price_import: PriceBindingConfig
    price_export: PriceBindingConfig
    zero_price_export_preference: Literal["export", "curtail"] = "export"
    import_forbidden_periods: list[TimeWindow] = []

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: str) -> str:
        return normalize_registry_key(value)

    def input_requirements(self) -> tuple[InputRequirement, ...]:
        requirements = [
            InputRequirement(
                self.price_import.source,
                ForecastInputConfig,
                InputValueKind.PRICE,
            ),
            InputRequirement(
                self.price_export.source,
                ForecastInputConfig,
                InputValueKind.PRICE,
            ),
        ]
        if self.realtime_grid_power is not None:
            requirements.append(
                InputRequirement(
                    self.realtime_grid_power,
                    ScalarInputConfig,
                    InputValueKind.POWER,
                )
            )
        return tuple(requirements)


class LoadComponentConfig(BaseModel):
    type: Literal["load"]
    connection: str = Field(min_length=1)
    name: str = Field(min_length=1)
    power: InputReference

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: str) -> str:
        return normalize_registry_key(value)

    def input_requirements(self) -> tuple[InputRequirement, ...]:
        return (
            InputRequirement(
                self.power,
                ForecastInputConfig,
                InputValueKind.POWER,
            ),
        )


class InverterComponentConfig(BaseModel):
    type: Literal["inverter"]
    connection: str = Field(min_length=1)
    name: str = Field(min_length=1)
    peak_power_kw: float = Field(ge=0)
    curtailment: Literal["load-aware", "binary"] | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: str) -> str:
        return normalize_registry_key(value)

    def input_requirements(self) -> tuple[InputRequirement, ...]:
        return ()


class BatteryComponentConfig(BaseModel):
    type: Literal["battery"]
    connection: str = Field(min_length=1)
    name: str = Field(min_length=1)
    capacity_kwh: float = Field(ge=0)
    storage_efficiency_pct: float = Field(gt=0, le=100)
    charge_cost_per_kwh: float = Field(default=0.0, ge=0)
    discharge_cost_per_kwh: float = Field(default=0.0, ge=0)
    stored_energy_value: StoredEnergyValueConfig
    min_soc_pct: float = Field(ge=0, le=100)
    max_soc_pct: float = Field(ge=0, le=100)
    reserve_soc_pct: float = Field(ge=0, le=100)
    max_charge_kw: float | None = Field(default=None, ge=0)
    max_discharge_kw: float | None = Field(default=None, ge=0)
    state_of_charge_pct: InputReference
    realtime_power: InputReference

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: str) -> str:
        return normalize_registry_key(value)

    @model_validator(mode="after")
    def _validate_soc_bounds(self) -> Self:
        if self.min_soc_pct > self.max_soc_pct:
            raise ValueError("min_soc_pct must be <= max_soc_pct")
        if self.reserve_soc_pct > self.max_soc_pct:
            raise ValueError("reserve_soc_pct must be <= max_soc_pct")
        return self

    def input_requirements(self) -> tuple[InputRequirement, ...]:
        requirements: list[InputRequirement] = [
            InputRequirement(
                self.state_of_charge_pct,
                ScalarInputConfig,
                InputValueKind.PERCENTAGE,
            ),
            InputRequirement(
                self.realtime_power,
                ScalarInputConfig,
                InputValueKind.POWER,
            ),
        ]
        requirements.append(
            InputRequirement(
                self.stored_energy_value.source,
                ForecastInputConfig,
                InputValueKind.PRICE,
            )
        )
        return tuple(requirements)


class PvComponentConfig(BaseModel):
    type: Literal["pv"]
    connection: str = Field(min_length=1)
    name: str | None = None
    forecast: InputReference
    forecast_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: str) -> str:
        return normalize_registry_key(value)

    def input_requirements(self) -> tuple[InputRequirement, ...]:
        return (
            InputRequirement(
                self.forecast,
                ForecastInputConfig,
                InputValueKind.POWER,
            ),
        )


class SocIncentive(BaseModel):
    target_soc_pct: float = Field(ge=0, le=100)
    incentive: float

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ControlledEvComponentConfig(BaseModel):
    type: Literal["load_controlled_ev"]
    connection: str = Field(min_length=1)
    name: str = Field(min_length=1)
    min_power_kw: float = Field(ge=0)
    max_power_kw: float = Field(ge=0)
    energy_kwh: float = Field(ge=0)
    connected: InputReference
    can_connect: InputReference | None = None
    allowed_connect_times: list[TimeWindow] = []
    connect_grace_minutes: int = Field(default=0, ge=0)
    realtime_power: InputReference
    state_of_charge_pct: InputReference
    soc_incentives: list[SocIncentive] = []
    switch_penalty: float = Field(default=0.0, ge=0)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("connection")
    @classmethod
    def _validate_connection(cls, value: str) -> str:
        return normalize_registry_key(value)

    @model_validator(mode="after")
    def _validate_power_bounds(self) -> Self:
        if self.min_power_kw > self.max_power_kw:
            raise ValueError("min_power_kw must be <= max_power_kw")
        return self

    def input_requirements(self) -> tuple[InputRequirement, ...]:
        requirements = [
            InputRequirement(
                self.connected,
                ScalarInputConfig,
                InputValueKind.BOOLEAN,
            ),
            InputRequirement(
                self.realtime_power,
                ScalarInputConfig,
                InputValueKind.POWER,
            ),
            InputRequirement(
                self.state_of_charge_pct,
                ScalarInputConfig,
                InputValueKind.PERCENTAGE,
            ),
        ]
        if self.can_connect is not None:
            requirements.append(
                InputRequirement(
                    self.can_connect,
                    ScalarInputConfig,
                    InputValueKind.BOOLEAN,
                )
            )
        return tuple(requirements)


PlantComponentConfig = Annotated[
    SwitchboardComponentConfig
    | GridComponentConfig
    | LoadComponentConfig
    | InverterComponentConfig
    | BatteryComponentConfig
    | PvComponentConfig
    | ControlledEvComponentConfig,
    Field(discriminator="type"),
]
