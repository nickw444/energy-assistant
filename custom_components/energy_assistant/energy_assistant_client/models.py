"""Pydantic models for the Energy Assistant API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

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


class GridTimestepPlan(BaseModel):
    import_kw: float
    export_kw: float
    net_kw: float
    import_allowed: bool | None
    import_violation_kw: float | None

    model_config = ConfigDict(extra="forbid")


class InverterTimestepPlan(BaseModel):
    name: str
    pv_kw: float | None
    pv_curtail_kw: float | None = None
    ac_net_kw: float
    battery_charge_kw: float | None
    battery_discharge_kw: float | None
    battery_soc_kwh: float | None
    battery_soc_pct: float | None
    curtailment: bool | None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvTimestepPlan(BaseModel):
    name: str
    charge_kw: float
    soc_kwh: float
    soc_pct: float | None
    connected: bool

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoadsTimestepPlan(BaseModel):
    base_kw: float
    evs: dict[str, EvTimestepPlan]
    total_kw: float

    model_config = ConfigDict(extra="forbid")


class EconomicsTimestepPlan(BaseModel):
    price_import: float
    price_export: float
    price_import_effective: float
    price_export_effective: float
    segment_cost: float
    cumulative_cost: float

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
    status: PlanStatus
    objective_value: float | None
    timings: EmsPlanTimings
    timesteps: list[TimestepPlan]

    model_config = ConfigDict(extra="forbid")


class PlanIntentMode(StrEnum):
    BACKUP = "Back-up"
    FORCE_CHARGE = "Force Charge"
    FORCE_DISCHARGE = "Force Discharge"
    EXPORT_PRIORITY = "Export Priority"
    SELF_USE = "Self Use"


class InverterPlanIntent(BaseModel):
    mode: PlanIntentMode
    export_limit_kw: float
    force_charge_kw: float
    force_discharge_kw: float

    model_config = ConfigDict(extra="forbid")


class LoadPlanIntent(BaseModel):
    charge_kw: float
    charge_on: bool

    model_config = ConfigDict(extra="forbid")


class PlanIntent(BaseModel):
    inverters: dict[str, InverterPlanIntent]
    loads: dict[str, LoadPlanIntent]

    model_config = ConfigDict(extra="forbid")


class PlanLatestResponse(BaseModel):
    run: PlanRunState
    plan: EmsPlanOutput
    intent: PlanIntent

    model_config = ConfigDict(extra="forbid")


class PlanAwaitResponse(BaseModel):
    run: PlanRunState
    plan: EmsPlanOutput
    intent: PlanIntent

    model_config = ConfigDict(extra="forbid")


class TerminalSocConfig(BaseModel):
    mode: Literal["hard", "adaptive"] = "adaptive"
    penalty_per_kwh: float | Literal["mean", "median"] | None = None

    model_config = ConfigDict(extra="forbid")


class EmsConfig(BaseModel):
    timestep_minutes: int
    min_horizon_minutes: int
    high_res_timestep_minutes: int | None = None
    high_res_horizon_minutes: int | None = None
    terminal_soc: TerminalSocConfig = Field(default_factory=TerminalSocConfig)

    model_config = ConfigDict(extra="forbid")
