from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.functional_serializers import PlainSerializer

from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
Rounded3 = Annotated[
    float,
    PlainSerializer(lambda v: round(v, 3), return_type=float, when_used="json"),
]
Rounded3Opt = Annotated[
    float | None,
    PlainSerializer(
        lambda v: None if v is None else round(v, 3),
        return_type=float | None,
        when_used="json",
    ),
]

EmsPlanStatus = Literal[
    "Optimal",
    "Infeasible",
    "Unbounded",
    "Undefined",
    "Not Solved",
    "Unknown",
]


class GridTimestepPlan(BaseModel):
    import_kw: Rounded3
    export_kw: Rounded3
    net_kw: Rounded3
    import_allowed: bool | None = None
    import_violation_kw: Rounded3Opt = None

    model_config = ConfigDict(extra="forbid")


class InverterTimestepPlan(BaseModel):
    name: str
    pv_kw: Rounded3Opt = None
    ac_net_kw: Rounded3
    battery_charge_kw: Rounded3Opt = None
    battery_discharge_kw: Rounded3Opt = None
    battery_soc_kwh: Rounded3Opt = None
    battery_soc_pct: Rounded3Opt = None
    curtailment: bool | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvTimestepPlan(BaseModel):
    name: str
    charge_kw: Rounded3
    soc_kwh: Rounded3
    soc_pct: Rounded3Opt = None
    connected: bool

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoadsTimestepPlan(BaseModel):
    base_kw: Rounded3
    evs: dict[str, EvTimestepPlan]
    total_kw: Rounded3

    model_config = ConfigDict(extra="forbid")


class EconomicsTimestepPlan(BaseModel):
    price_import: float
    price_export: float
    segment_cost: Rounded3
    cumulative_cost: Rounded3

    model_config = ConfigDict(extra="forbid")


class TimestepPlan(BaseModel):
    index: int
    start: datetime
    end: datetime
    duration_s: float
    grid: GridTimestepPlan
    inverters: dict[str, InverterTimestepPlan]
    loads: LoadsTimestepPlan
    economics: EconomicsTimestepPlan

    model_config = ConfigDict(extra="forbid")


class EmsPlanTimings(BaseModel):
    build_seconds: float
    solve_seconds: float
    total_seconds: float

    model_config = ConfigDict(extra="forbid")


class EmsPlanOutput(BaseModel):
    generated_at: datetime
    status: EmsPlanStatus
    objective_value: Rounded3Opt = None
    timings: EmsPlanTimings
    timesteps: list[TimestepPlan]

    model_config = ConfigDict(extra="forbid")


@dataclass(slots=True)
class ResolvedForecasts:
    grid_price_import: list[PriceForecastInterval]
    grid_price_export: list[PriceForecastInterval]
    load: list[PowerForecastInterval]
    inverters_pv: dict[str, list[PowerForecastInterval]]
    min_coverage_intervals: int
