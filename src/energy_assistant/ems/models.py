from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_serializers import PlainSerializer

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


class EmsPlanTimings(BaseModel):
    build_seconds: float
    solve_seconds: float
    total_seconds: float

    model_config = ConfigDict(extra="forbid")


class EmsSeriesPoint(BaseModel):
    time: datetime
    value: float | bool

    model_config = ConfigDict(extra="forbid")


class PlanIntentMode(StrEnum):
    BACKUP = "Back-up"
    FORCE_CHARGE = "Force Charge"
    FORCE_DISCHARGE = "Force Discharge"
    EXPORT_PRIORITY = "Export Priority"
    SELF_USE = "Self Use"


class InverterIntent(BaseModel):
    mode: PlanIntentMode
    export_limit_kw: Rounded3
    force_charge_kw: Rounded3
    force_discharge_kw: Rounded3

    model_config = ConfigDict(extra="forbid")


class LoadControlledEvIntent(BaseModel):
    charge_kw: Rounded3
    charge_on: bool

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
    intent: InverterIntent

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
    intent: LoadControlledEvIntent

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
    status: EmsPlanStatus
    objective_value: Rounded3Opt = None
    timings: EmsPlanTimings
    components: dict[str, ComponentPlan]

    model_config = ConfigDict(extra="forbid")
