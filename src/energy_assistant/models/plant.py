import calendar
import re
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from energy_assistant.lib.source_resolver.hass_source import (
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


def _default_import_forbidden_periods() -> list[TimeWindow]:
    return []


class GridPriceRiskConfig(BaseModel):
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


class GridConfig(BaseModel):
    max_import_kw: float = Field(ge=0)
    max_export_kw: float = Field(ge=0)
    realtime_grid_power: HomeAssistantPowerKwEntitySource
    realtime_price_import: HomeAssistantCurrencyEntitySource
    realtime_price_export: HomeAssistantCurrencyEntitySource
    price_import_forecast: HomeAssistantAmberElectricForecastSource
    price_export_forecast: HomeAssistantAmberElectricForecastSource
    # Grid price bias: sign-aware premium on positive imports and discount on
    # positive exports (negative prices move toward/away from zero accordingly).
    grid_price_bias_pct: float = Field(default=0.0, ge=0, le=100)
    # When export price is exactly zero, apply a tiny bonus or penalty to break ties
    # between export and curtailment.
    zero_price_export_preference: Literal["export", "curtail"] = "export"
    # Forecast price risk bias (ramps from start after minutes over duration).
    grid_price_risk: GridPriceRiskConfig | None = None
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
    charge_cost_per_kwh: float = Field(default=0.0, ge=0)
    discharge_cost_per_kwh: float = Field(default=0.0, ge=0)
    # Value assigned to each kWh of stored energy at horizon end.
    # When set, the objective includes a reward for terminal SoC, incentivizing
    # higher battery charging when export prices are low.
    # Default: None (disabled); typical value: 0.08-0.15 $/kWh.
    soc_value_per_kwh: float | None = Field(default=None, ge=0)
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
