import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantHistoricalAverageForecastSource,
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
    HomeAssistantSolcastForecastSource,
)


class TimeWindow(BaseModel):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _default_import_forbidden_periods() -> list[TimeWindow]:
    return []


class GridConfig(BaseModel):
    max_import_kw: float = Field(ge=0)
    max_export_kw: float = Field(ge=0)
    realtime_grid_power: HomeAssistantPowerKwEntitySource
    realtime_price_import: HomeAssistantCurrencyEntitySource
    realtime_price_export: HomeAssistantCurrencyEntitySource
    price_import_forecast: HomeAssistantAmberElectricForecastSource
    price_export_forecast: HomeAssistantAmberElectricForecastSource
    import_forbidden_periods: list[TimeWindow] = Field(
        default_factory=_default_import_forbidden_periods
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlantLoadConfig(BaseModel):
    realtime_load_power: HomeAssistantPowerKwEntitySource
    forecast: HomeAssistantHistoricalAverageForecastSource
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PvConfig(BaseModel):
    realtime_power: HomeAssistantPowerKwEntitySource | None = None
    forecast: HomeAssistantSolcastForecastSource

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(ge=0)
    storage_efficiency_pct: float = Field(gt=0, le=100)
    wear_cost_per_kwh: float = Field(default=0.0, ge=0)
    # Extra cost per kWh applied to battery -> grid export (part of economics).
    # Use when you want self-consumption to win over small arbitrage spreads but still
    # allow export at sufficiently high prices.
    export_margin_per_kwh: float = Field(default=0.0, ge=0)
    min_soc_pct: float = Field(ge=0, le=100)
    max_soc_pct: float = Field(ge=0, le=100)
    reserve_soc_pct: float = Field(ge=0, le=100)
    max_charge_kw: float | None = Field(default=None, ge=0)
    max_discharge_kw: float | None = Field(default=None, ge=0)
    state_of_charge_pct: HomeAssistantPercentageEntitySource
    realtime_power: HomeAssistantPowerKwEntitySource

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_soc_bounds(self) -> Self:
        if self.min_soc_pct > self.max_soc_pct:
            raise ValueError("min_soc_pct must be <= max_soc_pct")
        if self.reserve_soc_pct > self.max_soc_pct:
            raise ValueError("reserve_soc_pct must be <= max_soc_pct")
        return self


class InverterConfig(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    peak_power_kw: float = Field(ge=0)
    curtailment: Literal["load-aware", "binary"] | None = None
    # Cost per kWh of curtailed PV; should exceed battery wear_cost_per_kwh
    # so the solver prefers charging over curtailing. Default 0.03 (3c/kWh).
    curtailment_cost_per_kwh: float = Field(default=0.0, ge=0)
    pv: PvConfig
    battery: BatteryConfig | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", value):
            raise ValueError("id must be lowercase letters, numbers, and underscores")
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not any(ch.isalpha() for ch in value):
            raise ValueError("name must include at least one letter")
        return value


class PlantConfig(BaseModel):
    grid: GridConfig
    load: PlantLoadConfig
    inverters: list[InverterConfig]

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_inverter_ids_unique(self) -> Self:
        ids = [inv.id for inv in self.inverters]
        if len(ids) != len(set(ids)):
            raise ValueError("inverter ids must be unique")
        return self
