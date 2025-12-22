from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hass_energy.models.sources import (
    HomeAssistantEntitySource,
    HomeAssistantSolcastForecastSource,
    PriceForecastSource,
)

class TimeWindow(BaseModel):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GridConfig(BaseModel):
    max_import_kw: float = Field(ge=0)
    max_export_kw: float = Field(ge=0)
    realtime_grid_power: HomeAssistantEntitySource
    realtime_price_import: HomeAssistantEntitySource
    realtime_price_export: HomeAssistantEntitySource
    price_import_forecast: PriceForecastSource
    price_export_forecast: PriceForecastSource
    import_forbidden_periods: list[TimeWindow] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlantLoadConfig(BaseModel):
    realtime_load_power: HomeAssistantEntitySource
    load_forecast: HomeAssistantEntitySource | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PvConfig(BaseModel):
    capacity_kw: float = Field(ge=0)
    realtime_power: HomeAssistantEntitySource | None = None
    forecast: HomeAssistantSolcastForecastSource | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(ge=0)
    min_soc_pct: float = Field(ge=0, le=100)
    max_soc_pct: float = Field(ge=0, le=100)
    reserve_soc_pct: float = Field(ge=0, le=100)
    max_charge_kw: float | None = Field(default=None, ge=0)
    max_discharge_kw: float | None = Field(default=None, ge=0)
    state_of_charge_pct: HomeAssistantEntitySource
    realtime_power: HomeAssistantEntitySource
    dc_efficiency_pct: float | None = Field(default=None, ge=0, le=100)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_soc_bounds(self) -> "BatteryConfig":
        if self.min_soc_pct > self.max_soc_pct:
            raise ValueError("min_soc_pct must be <= max_soc_pct")
        if self.reserve_soc_pct > self.max_soc_pct:
            raise ValueError("reserve_soc_pct must be <= max_soc_pct")
        return self


class InverterConfig(BaseModel):
    name: str = Field(min_length=1)
    peak_power_kw: float = Field(ge=0)
    ac_efficiency_pct: float = Field(ge=0, le=100)
    pv: list[PvConfig]
    battery: list[BatteryConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlantConfig(BaseModel):
    grid: GridConfig
    load: PlantLoadConfig
    inverters: list[InverterConfig]

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
