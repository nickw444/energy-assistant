from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

import pulp

from hass_energy.config import EnergySystemConfig
from hass_energy.worker.milp.compiler import CompiledModel, ModelCompiler


class ParsedForecast(TypedDict):
    start: str
    end: str
    value: float
    unit: str


class HorizonSlot(TypedDict):
    start: str
    end: str
    duration_h: float
    import_price: float
    export_price: float
    pv_kw: float
    load_kw: float


@dataclass
class BatteryInputs:
    name: str
    soc_init_kwh: float
    soc_min_kwh: float
    soc_max_kwh: float
    soc_reserve_kwh: float
    charge_efficiency: float
    discharge_efficiency: float
    charge_power_max_kw: float
    discharge_power_max_kw: float


@dataclass
class EvInputs:
    name: str
    max_power_kw: float
    availability: list[bool]
    target_energy_kwh: float | None
    value_per_kwh: float | None


@dataclass
class DeferrableLoadInputs:
    name: str
    power_kw: float
    availability: list[bool]
    required_steps: int


@dataclass
class GridLimits:
    import_max_kw: float | None
    export_max_kw: float | None
    import_allowed: list[bool] | None


class MilpPlanner:
    """Cost-minimizing planner using price, load, and PV forecasts."""

    def __init__(
        self,
        compiler: ModelCompiler | None = None,
        *,
        default_import_price: float = 0.3,
        default_export_price: float = 0.0,
    ) -> None:
        self._solver = pulp.PULP_CBC_CMD(msg=False)
        self.compiler = compiler or ModelCompiler()
        self.default_import_price = default_import_price
        self.default_export_price = default_export_price

    def generate_plan(
        self,
        config: EnergySystemConfig,
        realtime_state: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        compiled: CompiledModel = self.compiler.compile(config)
        slots = self._build_slots(realtime_state, config)
        batteries = self._parse_batteries(realtime_state)
        evs = self._parse_evs(realtime_state, len(slots))
        deferrables = self._parse_deferrables(realtime_state, len(slots))
        grid_limits = self._parse_grid_limits(realtime_state, len(slots))
        self_use_incentive = self._parse_float(
            realtime_state.get("self_use_incentive_per_kwh"),
            default=0.0,
        )

        # Decision variables
        grid_power: list[pulp.LpVariable] = [
            pulp.LpVariable(f"grid_kw_{idx}") for idx in range(len(slots))
        ]
        grid_import: list[pulp.LpVariable] = [
            pulp.LpVariable(f"grid_import_kw_{idx}", lowBound=0)
            for idx in range(len(slots))
        ]
        grid_export: list[pulp.LpVariable] = [
            pulp.LpVariable(f"grid_export_kw_{idx}", lowBound=0)
            for idx in range(len(slots))
        ]
        pv_curt = [pulp.LpVariable(f"pv_curt_kw_{idx}", lowBound=0) for idx in range(len(slots))]

        # Batteries
        battery_power: dict[str, list[pulp.LpVariable]] = {}
        battery_charge: dict[str, list[pulp.LpVariable]] = {}
        battery_discharge: dict[str, list[pulp.LpVariable]] = {}
        battery_is_charge: dict[str, list[pulp.LpVariable]] = {}
        battery_is_discharge: dict[str, list[pulp.LpVariable]] = {}
        battery_soc: dict[str, list[pulp.LpVariable]] = {}

        for battery in batteries:
            battery_power[battery.name] = [
                pulp.LpVariable(
                    f"{battery.name}_kw_{idx}",
                    lowBound=-battery.discharge_power_max_kw,
                    upBound=battery.charge_power_max_kw,
                )
                for idx in range(len(slots))
            ]
            battery_charge[battery.name] = [
                pulp.LpVariable(
                    f"{battery.name}_chg_kw_{idx}",
                    lowBound=0,
                )
                for idx in range(len(slots))
            ]
            battery_discharge[battery.name] = [
                pulp.LpVariable(
                    f"{battery.name}_dis_kw_{idx}",
                    lowBound=0,
                )
                for idx in range(len(slots))
            ]
            battery_is_charge[battery.name] = [
                pulp.LpVariable(
                    f"{battery.name}_is_chg_{idx}",
                    lowBound=0,
                    upBound=1,
                    cat="Binary",
                )
                for idx in range(len(slots))
            ]
            battery_is_discharge[battery.name] = [
                pulp.LpVariable(
                    f"{battery.name}_is_dis_{idx}",
                    lowBound=0,
                    upBound=1,
                    cat="Binary",
                )
                for idx in range(len(slots))
            ]
            # SOC has len(slots)+1 to capture terminal state.
            battery_soc[battery.name] = [
                pulp.LpVariable(f"{battery.name}_soc_kwh_{idx}") for idx in range(len(slots) + 1)
            ]

        # EVs
        ev_power: dict[str, list[pulp.LpVariable]] = {
            ev.name: [
                pulp.LpVariable(
                    f"{ev.name}_kw_{idx}",
                    lowBound=0,
                )
                for idx in range(len(slots))
            ]
            for ev in evs
        }

        # Deferrable loads
        deferrable_on: dict[str, list[pulp.LpVariable]] = {
            load.name: [
                pulp.LpVariable(f"{load.name}_on_{idx}", lowBound=0, upBound=1, cat="Binary")
                for idx in range(len(slots))
            ]
            for load in deferrables
        }

        # Objective: minimize cost of imports minus revenue of exports.
        problem = pulp.LpProblem("EnergyPlan", pulp.LpMinimize)
        objective_terms: list[pulp.LpAffineExpression] = []
        for idx, slot in enumerate(slots):
            term = cast(
                pulp.LpAffineExpression,
                slot["duration_h"]
                * (
                    slot["import_price"] * grid_import[idx]
                    - slot["export_price"] * grid_export[idx]
                ),
            )
            objective_terms.append(term)

            if self_use_incentive > 0:
                objective_terms.append(
                    cast(
                        pulp.LpAffineExpression,
                        self_use_incentive * grid_export[idx] * slot["duration_h"],
                    )
                )

            # EV benefit (negative cost)
            for ev in evs:
                value = ev.value_per_kwh
                if value is None:
                    continue
                ev_term = cast(
                    pulp.LpAffineExpression,
                    -1 * value * ev_power[ev.name][idx] * slot["duration_h"],
                )
                objective_terms.append(ev_term)

        problem += pulp.lpSum(objective_terms)

        # Constraints per slot.
        for idx, slot in enumerate(slots):
            # Power balance: PV (minus curtailment) + imports covers load + exports.
            battery_dis_sum = (
                pulp.lpSum([battery_discharge[b.name][idx] for b in batteries])
                if batteries
                else 0
            )
            battery_chg_sum = (
                pulp.lpSum([battery_charge[b.name][idx] for b in batteries])
                if batteries
                else 0
            )
            ev_sum = pulp.lpSum([ev_power[ev.name][idx] for ev in evs]) if evs else 0
            if deferrables:
                deferrable_terms: list[pulp.LpAffineExpression] = [
                    cast(
                        pulp.LpAffineExpression,
                        deferrable_on[load.name][idx] * load.power_kw,
                    )
                    for load in deferrables
                ]
                deferrable_sum = pulp.lpSum(deferrable_terms)
            else:
                deferrable_sum = 0

            problem += (
                slot["pv_kw"] - pv_curt[idx] + grid_import[idx]
                + battery_dis_sum
                == slot["load_kw"]
                + deferrable_sum
                + ev_sum
                + battery_chg_sum
                + grid_export[idx]
            ), f"power_balance_{idx}"

            # Grid power definition (positive import, negative export).
            problem += (
                grid_power[idx] == grid_import[idx] - grid_export[idx]
            ), f"grid_power_def_{idx}"

            # Curtailment bounded by available PV.
            problem += pv_curt[idx] <= slot["pv_kw"], f"pv_curt_bound_{idx}"

            # Grid limits and availability.
            if grid_limits.import_max_kw is not None:
                problem += (
                    grid_import[idx] <= grid_limits.import_max_kw
                ), f"import_limit_{idx}"
            if grid_limits.export_max_kw is not None:
                problem += (
                    grid_export[idx] <= grid_limits.export_max_kw
                ), f"export_limit_{idx}"
            if grid_limits.import_allowed:
                allow = 1 if grid_limits.import_allowed[idx] else 0
                cap = grid_limits.import_max_kw if grid_limits.import_max_kw is not None else 1e6
                problem += grid_import[idx] <= cap * allow, f"import_allowed_{idx}"

            # Battery constraints
            for battery in batteries:
                # Power bounds with mode exclusivity.
                problem += (
                    battery_charge[battery.name][idx]
                    <= battery.charge_power_max_kw * battery_is_charge[battery.name][idx]
                ), f"{battery.name}_chg_cap_{idx}"
                problem += (
                    battery_discharge[battery.name][idx]
                    <= battery.discharge_power_max_kw * battery_is_discharge[battery.name][idx]
                ), f"{battery.name}_dis_cap_{idx}"
                problem += (
                    battery_is_charge[battery.name][idx]
                    + battery_is_discharge[battery.name][idx]
                    <= 1
                ), f"{battery.name}_mode_exclusive_{idx}"
                problem += (
                    battery_power[battery.name][idx]
                    == battery_charge[battery.name][idx] - battery_discharge[battery.name][idx]
                ), f"{battery.name}_power_def_{idx}"

                # SOC dynamics
                duration = slot["duration_h"]
                problem += (
                    battery_soc[battery.name][idx + 1]
                    == battery_soc[battery.name][idx]
                    + battery_charge[battery.name][idx] * duration * battery.charge_efficiency
                    - battery_discharge[battery.name][idx] * duration / battery.discharge_efficiency
                ), f"{battery.name}_soc_update_{idx}"

                # SOC bounds
                problem += (
                    battery_soc[battery.name][idx + 1] >= battery.soc_min_kwh
                ), f"{battery.name}_soc_min_{idx}"
                problem += (
                    battery_soc[battery.name][idx + 1] >= battery.soc_reserve_kwh
                ), f"{battery.name}_soc_reserve_{idx}"
                problem += (
                    battery_soc[battery.name][idx + 1] <= battery.soc_max_kwh
                ), f"{battery.name}_soc_max_{idx}"

            # EV availability
            for ev in evs:
                availability = ev.availability[idx] if idx < len(ev.availability) else True
                limit = ev.max_power_kw if availability else 0.0
                problem += (
                    ev_power[ev.name][idx] <= limit
                ), f"{ev.name}_availability_{idx}"

            # Deferrable availability
            for load in deferrables:
                availability = load.availability[idx] if idx < len(load.availability) else False
                problem += (
                    deferrable_on[load.name][idx] <= (1 if availability else 0)
                ), f"{load.name}_availability_{idx}"

        # Battery initial SOC constraints (after per-slot loops so variables exist)
        for battery in batteries:
            problem += (
                battery_soc[battery.name][0] == battery.soc_init_kwh
            ), f"{battery.name}_soc_initial"

        # EV target energy caps
        for ev in evs:
            if ev.target_energy_kwh is None:
                continue
            energy_terms: list[pulp.LpAffineExpression] = [
                cast(
                    pulp.LpAffineExpression,
                    ev_power[ev.name][idx] * slots[idx]["duration_h"],
                )
                for idx in range(len(slots))
            ]
            energy_sum = pulp.lpSum(energy_terms)
            problem += (
                energy_sum <= ev.target_energy_kwh
            ), f"{ev.name}_energy_target"

        # Deferrable runtime minimums
        for load in deferrables:
            required = max(load.required_steps, 0)
            if required <= 0:
                continue
            on_sum = pulp.lpSum(
                [
                    cast(pulp.LpAffineExpression, deferrable_on[load.name][idx])
                    for idx in range(len(slots))
                ]
            )
            problem += (
                on_sum >= required
            ), f"{load.name}_min_runtime"

        status: int = problem.solve(self._solver)  # type: ignore[no-untyped-call]
        objective_value = cast(
            float | None, pulp.value(problem.objective)  # type: ignore[arg-type, no-untyped-call]
        )
        objective = float(objective_value) if objective_value is not None else None

        plan_slots: list[dict[str, Any]] = []
        total_import_kwh = 0.0
        total_export_kwh = 0.0
        total_cost = 0.0
        battery_out: dict[str, list[dict[str, float]]] = {
            battery.name: [] for battery in batteries
        }
        ev_out: dict[str, list[dict[str, float]]] = {ev.name: [] for ev in evs}
        deferrable_out: dict[str, list[dict[str, float | bool]]] = {
            load.name: [] for load in deferrables
        }
        for idx, slot in enumerate(slots):
            grid_kw = grid_power[idx].value() or 0.0
            import_kw = grid_import[idx].value() or 0.0
            export_kw = grid_export[idx].value() or 0.0
            curt_kw = pv_curt[idx].value() or 0.0
            slot_cost = (
                slot["import_price"] * import_kw - slot["export_price"] * export_kw
            ) * slot["duration_h"]
            total_import_kwh += import_kw * slot["duration_h"]
            total_export_kwh += export_kw * slot["duration_h"]
            total_cost += slot_cost

            for battery in batteries:
                batt_kw = battery_power[battery.name][idx].value() or 0.0
                soc_next = battery_soc[battery.name][idx + 1].value() or 0.0
                battery_out[battery.name].append(
                    {
                        "power_kw": batt_kw,
                        "soc_kwh": soc_next,
                    }
                )

            for ev in evs:
                ev_kw = ev_power[ev.name][idx].value() or 0.0
                ev_out[ev.name].append({"charge_kw": ev_kw})

            for load in deferrables:
                on = bool(deferrable_on[load.name][idx].value() or 0.0)
                deferrable_out[load.name].append({"on": on})

            plan_slots.append(
                {
                    "start": slot["start"],
                    "end": slot["end"],
                    "duration_h": slot["duration_h"],
                    "grid_kw": grid_kw,
                    "pv_kw": slot["pv_kw"],
                    "pv_curtail_kw": curt_kw,
                    "load_kw": slot["load_kw"],
                    "import_price": slot["import_price"],
                    "export_price": slot["export_price"],
                    "slot_cost": slot_cost,
                    "battery": {name: battery_out[name][idx] for name in battery_out},
                    "ev": {name: ev_out[name][idx] for name in ev_out},
                    "deferrable": {name: deferrable_out[name][idx] for name in deferrable_out},
                }
            )

        return {
            "generated_at": time.time(),
            "status": pulp.LpStatus[status],
            "objective": objective,
            "total_cost": total_cost,
            "total_import_kwh": total_import_kwh,
            "total_export_kwh": total_export_kwh,
            "slots": plan_slots,
            "metadata": {
                "objective": "minimize_cost",
                "forecast_window_hours": config.forecast_window_hours,
                "realtime_sample_size": len(realtime_state),
                "history_sample_size": len(history),
                "compiled": compiled.metadata,
                "self_use_incentive_per_kwh": self_use_incentive,
                "missing_inputs": self._missing_inputs(
                    realtime_state,
                    slots=slots,
                    batteries=batteries,
                    evs=evs,
                    deferrables=deferrables,
                    grid_limits=grid_limits,
                ),
            },
        }

    def _build_slots(
        self,
        realtime_state: dict[str, Any],
        config: EnergySystemConfig,
    ) -> list[HorizonSlot]:
        import_forecast: list[ParsedForecast] = self._parse_forecast_series(
            realtime_state.get("import_price_forecast"),
            default_value=realtime_state.get("import_price", self.default_import_price),
            default_unit="AUD/kWh",
        )
        export_forecast: list[ParsedForecast] = self._parse_forecast_series(
            realtime_state.get("export_price_forecast"),
            default_value=realtime_state.get("export_price", self.default_export_price),
            default_unit="AUD/kWh",
        )
        load_forecast: list[ParsedForecast] = self._parse_forecast_series(
            realtime_state.get("load_forecast"),
            default_value=realtime_state.get("load_power"),
            default_unit="kW",
        )
        pv_forecast: list[ParsedForecast] = self._parse_forecast_series(
            realtime_state.get("pv_forecast"),
            default_value=0.0,
            default_unit="kWh",
        )

        start_dt = self._infer_start_time(import_forecast) or datetime.now(UTC)
        horizon_hours = max(int(config.forecast_window_hours), 1)
        step_minutes = 5
        step = timedelta(minutes=step_minutes)
        total_steps = max(1, int(horizon_hours * 60 / step_minutes))

        # If no load forecast provided, assume current load persists across horizon.
        if not load_forecast and "load_power" in realtime_state:
            load_kw_default = float(realtime_state.get("load_power") or 0.0)
            load_forecast = [
                ParsedForecast(
                    start=str(imp["start"]),
                    end=str(imp["end"]),
                    value=load_kw_default,
                    unit="kW",
                )
                for imp in import_forecast
            ]

        import_timeline = self._build_timeline(import_forecast)
        export_timeline = self._build_timeline(export_forecast)
        load_timeline = self._build_timeline(load_forecast)
        pv_timeline = self._build_pv_timeline(pv_forecast)
        pv_now_kw = float(realtime_state.get("pv_power") or 0.0)

        # Fixed 5-minute timestep across the horizon.
        slots: list[HorizonSlot] = []
        for idx in range(total_steps):
            slot_start = start_dt + step * idx
            slot_end = slot_start + step
            duration_h = step.total_seconds() / 3600.0

            import_price = self._lookup_timeline_value(
                import_timeline,
                slot_start,
                default=float(realtime_state.get("import_price", self.default_import_price)),
            )
            export_price = self._lookup_timeline_value(
                export_timeline,
                slot_start,
                default=float(realtime_state.get("export_price", self.default_export_price)),
            )
            load_kw = self._lookup_timeline_value(
                load_timeline,
                slot_start,
                default=float(realtime_state.get("load_power") or 0.0),
            )
            load_kw = max(load_kw, 0.0)
            pv_kw = self._lookup_pv_kw(pv_timeline, slot_start, pv_now_kw)

            slots.append(
                {
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                    "duration_h": duration_h,
                    "import_price": import_price,
                    "export_price": export_price,
                    "pv_kw": pv_kw,
                    "load_kw": load_kw,
                }
            )
        return slots

    def _build_pv_timeline(
        self,
        pv_forecast: list[ParsedForecast],
    ) -> list[tuple[datetime, datetime, float, str]]:
        timeline: list[tuple[datetime, datetime, float, str]] = []
        for item in pv_forecast:
            start_dt = self._parse_iso(item["start"])
            end_dt = self._parse_iso(item["end"])
            if start_dt is None or end_dt is None:
                continue
            timeline.append((start_dt, end_dt, float(item["value"]), item["unit"]))
        timeline.sort(key=lambda entry: entry[0])
        return timeline

    def _build_timeline(
        self,
        forecast: list[ParsedForecast],
    ) -> list[tuple[datetime, datetime, float, str]]:
        timeline: list[tuple[datetime, datetime, float, str]] = []
        for item in forecast:
            start_dt = self._parse_iso(item["start"])
            end_dt = self._parse_iso(item["end"])
            if start_dt is None or end_dt is None:
                continue
            timeline.append((start_dt, end_dt, float(item["value"]), item["unit"]))
        timeline.sort(key=lambda entry: entry[0])
        return timeline

    def _lookup_pv_kw(
        self,
        pv_timeline: list[tuple[datetime, datetime, float, str]],
        slot_start: datetime,
        pv_now_kw: float,
    ) -> float:
        if pv_timeline and slot_start < pv_timeline[0][0]:
            return max(pv_now_kw, 0.0)
        for start_dt, end_dt, value, unit in pv_timeline:
            if start_dt <= slot_start < end_dt:
                duration_h = max((end_dt - start_dt).total_seconds(), 0) / 3600.0
                return self._pv_value_to_kw(value, unit, duration_h)
        return 0.0

    def _pv_value_to_kw(self, value: float, unit: str, duration_h: float) -> float:
        unit_lower = unit.lower()
        if "kwh" in unit_lower:
            if duration_h <= 0:
                return 0.0
            return value / duration_h
        if "kw" in unit_lower:
            return value
        if duration_h <= 0:
            return 0.0
        return value / duration_h

    def _parse_iso(self, value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _infer_start_time(self, forecast: list[ParsedForecast]) -> datetime | None:
        for item in forecast:
            start_dt = self._parse_iso(item["start"])
            if start_dt is not None:
                return start_dt
        return None

    def _lookup_timeline_value(
        self,
        timeline: list[tuple[datetime, datetime, float, str]],
        slot_start: datetime,
        *,
        default: float,
    ) -> float:
        if timeline and slot_start < timeline[0][0]:
            return default
        for start_dt, end_dt, value, _unit in timeline:
            if start_dt <= slot_start < end_dt:
                return value
        return default

    def _parse_float(self, value: Any, *, default: float) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _parse_forecast_series(
        self,
        raw: Any,
        *,
        default_value: float | None,
        default_unit: str,
    ) -> list[ParsedForecast]:
        if not isinstance(raw, list):
            return []
        forecast_items = cast(list[Any], raw)
        parsed: list[ParsedForecast] = []
        for item in forecast_items:
            if not isinstance(item, dict):
                continue
            item_dict: dict[str, Any] = item  # type: ignore[assignment]
            start_raw: Any = item_dict.get("start") or item_dict.get("start_time")
            end_raw: Any = item_dict.get("end") or item_dict.get("end_time")
            start_str = self._coerce_iso_str(start_raw)
            end_str = self._coerce_iso_str(end_raw)
            if not start_str or not end_str:
                continue

            value_raw = item_dict.get("value")
            if value_raw is None:
                value_raw = item_dict.get("price")
            if value_raw is None:
                value_raw = default_value
            if value_raw is None:
                continue
            try:
                value_f = float(value_raw)
            except (TypeError, ValueError):
                continue

            unit_raw: Any = item_dict.get("unit")
            unit = unit_raw if isinstance(unit_raw, str) and unit_raw else default_unit

            parsed.append(
                ParsedForecast(
                    start=start_str,
                    end=end_str,
                    value=value_f,
                    unit=unit,
                )
            )
        return parsed

    def _duration_hours(self, start: str, end: str) -> float:
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            delta = end_dt - start_dt
            return max(delta.total_seconds(), 0) / 3600.0
        except (TypeError, ValueError):
            return 1.0

    def _coerce_iso_str(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value)
        except Exception:
            return None

    def _parse_batteries(self, realtime_state: dict[str, Any]) -> list[BatteryInputs]:
        raw = realtime_state.get("batteries")
        if not isinstance(raw, list):
            raw = []
        batteries: list[BatteryInputs] = []
        raw_list = cast(list[Any], raw)
        for idx, entry in enumerate(raw_list):
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, Any], entry)
            try:
                batteries.append(
                    BatteryInputs(
                        name=str(entry_dict.get("name") or f"battery_{idx}"),
                        soc_init_kwh=float(entry_dict["soc_init_kwh"]),
                        soc_min_kwh=float(entry_dict.get("soc_min_kwh", 0.0)),
                        soc_max_kwh=float(entry_dict["soc_max_kwh"]),
                        soc_reserve_kwh=float(entry_dict.get("soc_reserve_kwh", 0.0)),
                        charge_efficiency=float(entry_dict.get("charge_efficiency", 0.95)),
                        discharge_efficiency=float(entry_dict.get("discharge_efficiency", 0.95)),
                        charge_power_max_kw=float(entry_dict.get("charge_power_max_kw", 0.0)),
                        discharge_power_max_kw=float(entry_dict.get("discharge_power_max_kw", 0.0)),
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue

        if batteries:
            return batteries

        # Fallback: derive a single battery from realtime SOC percentage and known capacity.
        soc_pct_raw = realtime_state.get("battery_soc")
        try:
            soc_pct = float(soc_pct_raw) if soc_pct_raw is not None else None
        except (TypeError, ValueError):
            soc_pct = None
        if soc_pct is None:
            return []

        capacity_kwh = 41.9
        soc_init_kwh = capacity_kwh * soc_pct / 100.0
        batteries.append(
            BatteryInputs(
                name="battery_main",
                soc_init_kwh=soc_init_kwh,
                soc_min_kwh=capacity_kwh * 0.10,
                soc_max_kwh=capacity_kwh * 1.0,
                soc_reserve_kwh=capacity_kwh * 0.20,
                charge_efficiency=0.97,
                discharge_efficiency=0.97,
                charge_power_max_kw=10.0,
                discharge_power_max_kw=10.0,
            )
        )
        return batteries

    def _parse_evs(self, realtime_state: dict[str, Any], horizon: int) -> list[EvInputs]:
        raw = realtime_state.get("evs")
        if not isinstance(raw, list):
            return []
        evs: list[EvInputs] = []
        raw_list = cast(list[Any], raw)
        for idx, entry in enumerate(raw_list):
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, Any], entry)
            availability = entry_dict.get("availability")
            availability_list_raw = (
                cast(list[Any], availability) if isinstance(availability, list) else []
            )
            availability_list = [bool(value) for value in availability_list_raw]
            if not availability_list:
                availability_list = [True] * horizon
            availability_list = availability_list[:horizon] + [True] * max(
                0,
                horizon - len(availability_list),
            )
            target_energy = entry_dict.get("target_energy_kwh")
            try:
                evs.append(
                    EvInputs(
                        name=str(entry_dict.get("name") or f"ev_{idx}"),
                        max_power_kw=float(entry_dict.get("max_power_kw", 0.0)),
                        availability=availability_list,
                        target_energy_kwh=(
                            float(target_energy) if target_energy is not None else None
                        ),
                        value_per_kwh=(
                            float(entry_dict["value_per_kwh"])
                            if "value_per_kwh" in entry_dict
                            else None
                        ),
                    )
                )
            except (TypeError, ValueError):
                continue
        return evs

    def _parse_deferrables(
        self,
        realtime_state: dict[str, Any],
        horizon: int,
    ) -> list[DeferrableLoadInputs]:
        raw = realtime_state.get("deferrable_loads")
        if not isinstance(raw, list):
            return []
        loads: list[DeferrableLoadInputs] = []
        raw_list = cast(list[Any], raw)
        for idx, entry in enumerate(raw_list):
            if not isinstance(entry, dict):
                continue
            entry_dict = cast(dict[str, Any], entry)
            availability = entry_dict.get("availability")
            availability_list_raw = (
                cast(list[Any], availability) if isinstance(availability, list) else []
            )
            availability_list = [bool(value) for value in availability_list_raw]
            if not availability_list:
                availability_list = [False] * horizon
            availability_list = availability_list[:horizon] + [False] * max(
                0,
                horizon - len(availability_list),
            )
            try:
                loads.append(
                    DeferrableLoadInputs(
                        name=str(entry_dict.get("name") or f"deferrable_{idx}"),
                        power_kw=float(entry_dict.get("power_kw", 0.0)),
                        availability=availability_list,
                        required_steps=int(entry_dict.get("required_steps", 0)),
                    )
                )
            except (TypeError, ValueError):
                continue
        return loads

    def _parse_grid_limits(self, realtime_state: dict[str, Any], horizon: int) -> GridLimits:
        import_limit = realtime_state.get("grid_import_limit_kw")
        export_limit = realtime_state.get("grid_export_limit_kw")
        import_allowed_raw = realtime_state.get("import_allowed")
        import_allowed: list[bool] | None = None
        if isinstance(import_allowed_raw, list):
            allowed_list = cast(list[Any], import_allowed_raw)
            bools = [bool(x) for x in allowed_list]
            import_allowed = bools[:horizon] + [True] * max(0, horizon - len(bools))
        else:
            import_allowed = [True] * horizon

        return GridLimits(
            import_max_kw=(
                float(import_limit)
                if isinstance(import_limit, (int, float))
                else 13.0
            ),
            export_max_kw=(
                float(export_limit)
                if isinstance(export_limit, (int, float))
                else 13.0
            ),
            import_allowed=import_allowed,
        )

    def _missing_inputs(
        self,
        realtime_state: dict[str, Any],
        *,
        slots: list[HorizonSlot],
        batteries: list[BatteryInputs],
        evs: list[EvInputs],
        deferrables: list[DeferrableLoadInputs],
        grid_limits: GridLimits,
    ) -> list[str]:
        missing: list[str] = []
        if not realtime_state.get("import_price_forecast"):
            missing.append("import_price_forecast")
        if not realtime_state.get("export_price_forecast"):
            missing.append("export_price_forecast")
        if not realtime_state.get("pv_forecast"):
            missing.append("pv_forecast (kWh over interval)")
        if grid_limits.import_max_kw is None:
            missing.append("grid_limits.import_max_kw")
        if grid_limits.export_max_kw is None:
            missing.append("grid_limits.export_max_kw")
        if not grid_limits.import_allowed:
            missing.append("import availability mask (A_import[t])")
        if not batteries:
            missing.append("battery parameters (SOC, power limits, efficiencies)")
        if not slots:
            missing.append("time discretization (forecast timeline)")
        return missing
