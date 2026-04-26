"""Pydantic models for the Energy Assistant API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

PlanRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
PlanStatus = Literal[
    "Optimal",
    "Infeasible",
    "Unbounded",
    "Undefined",
    "Not Solved",
    "Unknown",
]


class PlanRunState(BaseModel):
    run_id: str
    status: PlanRunStatus
    accepted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    message: str | None

    model_config = ConfigDict(extra="forbid")


class PlanRunResponse(BaseModel):
    run: PlanRunState
    already_running: bool

    model_config = ConfigDict(extra="forbid")


class EmsSeriesPoint(BaseModel):
    time: datetime
    value: float | bool

    model_config = ConfigDict(extra="forbid")


class EmsPlanTimings(BaseModel):
    build_seconds: float
    solve_seconds: float
    total_seconds: float

    model_config = ConfigDict(extra="forbid")


class SwitchboardComponentPlan(BaseModel):
    type: Literal["switchboard"] = "switchboard"

    model_config = ConfigDict(extra="forbid")


class GridComponentPlan(BaseModel):
    type: Literal["grid"] = "grid"
    price_import_raw: list[EmsSeriesPoint]
    price_export_raw: list[EmsSeriesPoint]
    price_import_effective: list[EmsSeriesPoint]
    price_export_effective: list[EmsSeriesPoint]
    import_allowed: list[EmsSeriesPoint]
    import_kw: list[EmsSeriesPoint]
    export_kw: list[EmsSeriesPoint]
    net_kw: list[EmsSeriesPoint]

    model_config = ConfigDict(extra="forbid")


class LoadComponentPlan(BaseModel):
    type: Literal["load"] = "load"
    power_kw: list[EmsSeriesPoint]

    model_config = ConfigDict(extra="forbid")


class InverterComponentPlan(BaseModel):
    type: Literal["inverter"] = "inverter"
    ac_net_kw: list[EmsSeriesPoint]

    model_config = ConfigDict(extra="forbid")


class PvComponentPlan(BaseModel):
    type: Literal["pv"] = "pv"
    available_kw: list[EmsSeriesPoint]
    actual_kw: list[EmsSeriesPoint]
    curtail_kw: list[EmsSeriesPoint]
    curtailment: list[EmsSeriesPoint]

    model_config = ConfigDict(extra="forbid")


class BatteryComponentPlan(BaseModel):
    type: Literal["battery"] = "battery"
    charge_kw: list[EmsSeriesPoint]
    discharge_kw: list[EmsSeriesPoint]
    soc_kwh: list[EmsSeriesPoint]
    soc_pct: list[EmsSeriesPoint]

    model_config = ConfigDict(extra="forbid")


class LoadControlledEvComponentPlan(BaseModel):
    type: Literal["load_controlled_ev"] = "load_controlled_ev"
    charge_kw: list[EmsSeriesPoint]
    soc_kwh: list[EmsSeriesPoint]
    soc_pct: list[EmsSeriesPoint]
    connected: list[EmsSeriesPoint]
    charge_allowed: list[EmsSeriesPoint]

    model_config = ConfigDict(extra="forbid")


ComponentPlan = Annotated[
    SwitchboardComponentPlan
    | GridComponentPlan
    | LoadComponentPlan
    | InverterComponentPlan
    | PvComponentPlan
    | BatteryComponentPlan
    | LoadControlledEvComponentPlan,
    Field(discriminator="type"),
]


class EmsPlanOutput(BaseModel):
    generated_at: datetime
    status: PlanStatus
    objective_value: float | None
    timings: EmsPlanTimings
    components: dict[str, ComponentPlan]

    model_config = ConfigDict(extra="forbid")


class PlanLatestResponse(BaseModel):
    run: PlanRunState
    plan: EmsPlanOutput

    model_config = ConfigDict(extra="forbid")


class PlanAwaitResponse(BaseModel):
    run: PlanRunState
    plan: EmsPlanOutput

    model_config = ConfigDict(extra="forbid")


class TerminalSocConfig(BaseModel):
    mode: Literal["hard", "adaptive"] = "adaptive"
    penalty_per_kwh: float | Literal["mean", "median"] | None = None

    model_config = ConfigDict(extra="forbid")


class EmsConfig(BaseModel):
    timestep_minutes: int
    horizon_minutes: int
    high_res_timestep_minutes: int | None = None
    high_res_horizon_minutes: int | None = None
    terminal_soc: TerminalSocConfig = Field(default_factory=TerminalSocConfig)

    model_config = ConfigDict(extra="forbid")
