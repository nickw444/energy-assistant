from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

import pulp

from hass_energy.models.config import EmsConfig
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
class GridLimits:
    import_max_kw: float | None
    export_max_kw: float | None
    import_allowed: list[bool]


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
    min_power_kw: float
    availability: list[bool]
    target_energy_kwh: float | None
    value_per_kwh: float | None
    switch_penalty: float


@dataclass
class CoreVariables:
    grid_import: list[pulp.LpVariable]
    grid_export: list[pulp.LpVariable]
    pv_curt: list[pulp.LpVariable]


class MilpPlanner:
    """Minimal cost-minimizing planner with grid + PV + base load balance."""

    def __init__(
        self,
        compiler: ModelCompiler | None = None,
        *,
        default_import_price: float = 0.3,
        default_export_price: float = 0.0,
        default_inverter_export_limit_kw: float = 10.0,
        default_inverter_charge_efficiency: float = 0.95,
        default_inverter_discharge_efficiency: float = 0.95,
        default_import_price_cap: float = 0.5,
        default_export_price_floor: float = 0.2,
    ) -> None:
        self._solver = pulp.PULP_CBC_CMD(msg=False)
        self.compiler = compiler or ModelCompiler()
        self.default_import_price = default_import_price
        self.default_export_price = default_export_price
        self.default_inverter_export_limit_kw = default_inverter_export_limit_kw
        self.default_inverter_charge_efficiency = default_inverter_charge_efficiency
        self.default_inverter_discharge_efficiency = default_inverter_discharge_efficiency
        self.default_import_price_cap = default_import_price_cap
        self.default_export_price_floor = default_export_price_floor

    def generate_plan(
        self,
        config: EmsConfig,
        realtime_state: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        compiled: CompiledModel = self.compiler.compile(config)
        slots = self._build_slots(realtime_state, config)
        grid_limits = self._parse_grid_limits(realtime_state, len(slots))
        batteries = self._parse_batteries(realtime_state)
        evs = self._parse_evs(realtime_state, len(slots))
        inverter_export_limit = self._parse_inverter_export_limit_kw(realtime_state)
        inverter_charge_eff, inverter_discharge_eff = self._parse_inverter_efficiencies(
            realtime_state
        )
        import_price_cap, export_price_floor = self._parse_price_limits(realtime_state)

        problem, variables, battery_vars = self._build_core_model(
            slots,
            grid_limits,
            batteries,
            evs,
            inverter_export_limit,
            inverter_charge_eff,
            inverter_discharge_eff,
            import_price_cap,
            export_price_floor,
        )

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

        for idx, slot in enumerate(slots):
            import_kw = variables.grid_import[idx].value() or 0.0
            export_kw = variables.grid_export[idx].value() or 0.0
            grid_kw = import_kw - export_kw
            curt_kw = variables.pv_curt[idx].value() or 0.0
            slot_cost = (
                slot["import_price"] * import_kw - slot["export_price"] * export_kw
            ) * slot["duration_h"]
            total_import_kwh += import_kw * slot["duration_h"]
            total_export_kwh += export_kw * slot["duration_h"]
            total_cost += slot_cost

            for battery in batteries:
                charge_kw = battery_vars["charge"][battery.name][idx].value() or 0.0
                discharge_kw = battery_vars["discharge"][battery.name][idx].value() or 0.0
                power_kw = discharge_kw - charge_kw
                soc_next = battery_vars["soc"][battery.name][idx + 1].value() or 0.0
                battery_out[battery.name].append(
                    {
                        "power_kw": power_kw,
                        "soc_kwh": soc_next,
                    }
                )

            for ev in evs:
                ev_kw = battery_vars["ev_power"][ev.name][idx].value() or 0.0
                ev_on = battery_vars["ev_on"][ev.name][idx].value() or 0.0
                ev_out[ev.name].append({"charge_kw": ev_kw, "on": bool(ev_on)})

            plan_slots.append(
                {
                    "start": slot["start"],
                    "end": slot["end"],
                    "duration_h": slot["duration_h"],
                    "grid_kw": grid_kw,
                    "grid_import_kw": import_kw,
                    "grid_export_kw": export_kw,
                    "pv_kw": slot["pv_kw"],
                    "pv_curtail_kw": curt_kw,
                    "load_kw": slot["load_kw"],
                    "import_price": slot["import_price"],
                    "export_price": slot["export_price"],
                    "slot_cost": slot_cost,
                    "battery": {name: battery_out[name][idx] for name in battery_out},
                    "ev": {name: ev_out[name][idx] for name in ev_out},
                    "deferrable": {},
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
                "interval_duration": config.interval_duration,
                "num_intervals": config.num_intervals,
                "realtime_sample_size": len(realtime_state),
                "history_sample_size": len(history),
                "compiled": compiled.metadata,
                "features": [
                    "core_balance",
                    "pv_curtailment",
                    "grid_limits"
                    if grid_limits.import_max_kw or grid_limits.export_max_kw
                    else "grid_unlimited",
                    "battery_soc" if batteries else "no_battery",
                    "ev_charging" if evs else "no_ev",
                    "inverter_export_limit"
                    if inverter_export_limit is not None
                    else "inverter_unlimited",
                    "inverter_efficiency",
                    "price_limits",
                ],
                "inverter_export_limit_kw": inverter_export_limit,
                "inverter_charge_efficiency": inverter_charge_eff,
                "inverter_discharge_efficiency": inverter_discharge_eff,
                "import_price_cap": import_price_cap,
                "export_price_floor": export_price_floor,
                "missing_inputs": self._missing_inputs(
                    realtime_state,
                    slots=slots,
                    grid_limits=grid_limits,
                    batteries=batteries,
                ),
            },
        }

    def _build_core_model(
        self,
        slots: list[HorizonSlot],
        grid_limits: GridLimits,
        batteries: list[BatteryInputs],
        evs: list[EvInputs],
        inverter_export_limit: float | None,
        inverter_charge_eff: float,
        inverter_discharge_eff: float,
        import_price_cap: float | None,
        export_price_floor: float | None,
    ) -> tuple[
        pulp.LpProblem,
        CoreVariables,
        dict[str, dict[str, list[pulp.LpVariable]]],
    ]:
        problem = pulp.LpProblem("EnergyPlan", pulp.LpMinimize)

        grid_import: list[pulp.LpVariable] = [
            pulp.LpVariable(f"grid_import_kw_{idx}", lowBound=0) for idx in range(len(slots))
        ]
        grid_export: list[pulp.LpVariable] = [
            pulp.LpVariable(f"grid_export_kw_{idx}", lowBound=0) for idx in range(len(slots))
        ]
        pv_curt: list[pulp.LpVariable] = [
            pulp.LpVariable(f"pv_curt_kw_{idx}", lowBound=0) for idx in range(len(slots))
        ]
        battery_charge: dict[str, list[pulp.LpVariable]] = {}
        battery_discharge: dict[str, list[pulp.LpVariable]] = {}
        battery_is_charge: dict[str, list[pulp.LpVariable]] = {}
        battery_is_discharge: dict[str, list[pulp.LpVariable]] = {}
        battery_soc: dict[str, list[pulp.LpVariable]] = {}
        ev_power: dict[str, list[pulp.LpVariable]] = {}
        ev_on: dict[str, list[pulp.LpVariable]] = {}
        ev_switch: dict[str, list[pulp.LpVariable | None]] = {}

        for battery in batteries:
            battery_charge[battery.name] = [
                pulp.LpVariable(f"{battery.name}_chg_kw_{idx}", lowBound=0)
                for idx in range(len(slots))
            ]
            battery_discharge[battery.name] = [
                pulp.LpVariable(f"{battery.name}_dis_kw_{idx}", lowBound=0)
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
            battery_soc[battery.name] = [
                pulp.LpVariable(f"{battery.name}_soc_kwh_{idx}", lowBound=0)
                for idx in range(len(slots) + 1)
            ]

        for ev in evs:
            ev_power[ev.name] = [
                pulp.LpVariable(f"{ev.name}_kw_{idx}", lowBound=0)
                for idx in range(len(slots))
            ]
            ev_on[ev.name] = [
                pulp.LpVariable(f"{ev.name}_on_{idx}", lowBound=0, upBound=1, cat="Binary")
                for idx in range(len(slots))
            ]
            ev_switch[ev.name] = [None]
            for idx in range(1, len(slots)):
                ev_switch[ev.name].append(
                    pulp.LpVariable(f"{ev.name}_switch_{idx}", lowBound=0, upBound=1, cat="Binary")
                )

        objective_terms: list[pulp.LpAffineExpression] = []
        for idx, slot in enumerate(slots):
            objective_terms.append(
                cast(
                    pulp.LpAffineExpression,
                    slot["duration_h"]
                    * (
                        slot["import_price"] * grid_import[idx]
                        - slot["export_price"] * grid_export[idx]
                    ),
                )
            )

            for ev in evs:
                value = ev.value_per_kwh
                if value is None:
                    continue
                objective_terms.append(
                    cast(
                        pulp.LpAffineExpression,
                        -1 * value * ev_power[ev.name][idx] * slot["duration_h"],
                    )
                )
            for ev in evs:
                if ev.switch_penalty <= 0:
                    continue
                switch_var = ev_switch[ev.name][idx]
                if switch_var is None:
                    continue
                objective_terms.append(
                    cast(
                        pulp.LpAffineExpression,
                        ev.switch_penalty * switch_var,
                    )
                )

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

            problem += (
                slot["pv_kw"]
                - pv_curt[idx]
                + grid_import[idx]
                + battery_dis_sum
                == slot["load_kw"] + battery_chg_sum + ev_sum + grid_export[idx]
            ), f"power_balance_{idx}"

            if inverter_export_limit is not None:
                problem += (
                    slot["pv_kw"] - pv_curt[idx] + battery_dis_sum
                    <= inverter_export_limit
                ), f"inverter_export_limit_{idx}"

            curtail_allowed = 1.0 if slot["export_price"] < 0 else 0.0
            problem += (
                pv_curt[idx] <= slot["pv_kw"] * curtail_allowed
            ), f"pv_curt_bound_{idx}"

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

            if import_price_cap is not None and slot["import_price"] > import_price_cap:
                problem += grid_import[idx] == 0, f"import_price_cap_{idx}"
            if export_price_floor is not None and slot["export_price"] < export_price_floor:
                problem += grid_export[idx] == 0, f"export_price_floor_{idx}"

            for battery in batteries:
                problem += (
                    battery_charge[battery.name][idx]
                    <= battery.charge_power_max_kw * battery_is_charge[battery.name][idx]
                ), f"{battery.name}_chg_cap_{idx}"
                problem += (
                    battery_discharge[battery.name][idx]
                    <= battery.discharge_power_max_kw
                    * battery_is_discharge[battery.name][idx]
                ), f"{battery.name}_dis_cap_{idx}"
                problem += (
                    battery_is_charge[battery.name][idx]
                    + battery_is_discharge[battery.name][idx]
                    <= 1
                ), f"{battery.name}_mode_exclusive_{idx}"

                duration = slot["duration_h"]
                problem += (
                    battery_soc[battery.name][idx + 1]
                    == battery_soc[battery.name][idx]
                    + battery_charge[battery.name][idx]
                    * duration
                    * battery.charge_efficiency
                    * inverter_charge_eff
                    - battery_discharge[battery.name][idx]
                    * duration
                    / (battery.discharge_efficiency * inverter_discharge_eff)
                ), f"{battery.name}_soc_update_{idx}"

                problem += (
                    battery_soc[battery.name][idx + 1] >= battery.soc_min_kwh
                ), f"{battery.name}_soc_min_{idx}"
                problem += (
                    battery_soc[battery.name][idx + 1] >= battery.soc_reserve_kwh
                ), f"{battery.name}_soc_reserve_{idx}"
                problem += (
                    battery_soc[battery.name][idx + 1] <= battery.soc_max_kwh
                ), f"{battery.name}_soc_max_{idx}"

            for ev in evs:
                availability = ev.availability[idx] if idx < len(ev.availability) else True
                limit = ev.max_power_kw if availability else 0.0
                min_power = ev.min_power_kw if availability else 0.0
                problem += (
                    ev_power[ev.name][idx] <= limit
                ), f"{ev.name}_availability_{idx}"
                problem += (
                    ev_on[ev.name][idx] <= (1 if availability else 0)
                ), f"{ev.name}_on_available_{idx}"
                problem += (
                    ev_power[ev.name][idx] <= limit * ev_on[ev.name][idx]
                ), f"{ev.name}_on_cap_{idx}"
                if min_power > 0:
                    problem += (
                        ev_power[ev.name][idx] >= min_power * ev_on[ev.name][idx]
                    ), f"{ev.name}_on_min_{idx}"

        problem += pulp.lpSum(objective_terms)

        for battery in batteries:
            problem += (
                battery_soc[battery.name][0] == battery.soc_init_kwh
            ), f"{battery.name}_soc_initial"
            problem += (
                battery_soc[battery.name][len(slots)] >= battery.soc_init_kwh
            ), f"{battery.name}_soc_terminal_min"

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

        for ev in evs:
            if ev.switch_penalty <= 0:
                continue
            for idx in range(1, len(slots)):
                switch_var = ev_switch[ev.name][idx]
                if switch_var is None:
                    continue
                problem += (
                    switch_var >= ev_on[ev.name][idx] - ev_on[ev.name][idx - 1]
                ), f"{ev.name}_switch_pos_{idx}"
                problem += (
                    switch_var >= ev_on[ev.name][idx - 1] - ev_on[ev.name][idx]
                ), f"{ev.name}_switch_neg_{idx}"

        return problem, CoreVariables(
            grid_import=grid_import,
            grid_export=grid_export,
            pv_curt=pv_curt,
        ), {
            "charge": battery_charge,
            "discharge": battery_discharge,
            "soc": battery_soc,
            "ev_power": ev_power,
            "ev_on": ev_on,
            "ev_switch": ev_switch,
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

        start_dt = self._infer_start_time(
            import_forecast,
            export_forecast,
            load_forecast,
            pv_forecast,
        ) or datetime.now(UTC)
        horizon_minutes = max(int(config.interval_duration * config.num_intervals), 1)
        step_minutes = 5
        step = timedelta(minutes=step_minutes)
        total_steps = max(1, int(horizon_minutes / step_minutes))

        import_timeline = self._build_timeline(import_forecast)
        export_timeline = self._build_timeline(export_forecast)
        load_timeline = self._build_timeline(load_forecast)
        pv_timeline = self._build_pv_timeline(pv_forecast)
        pv_now_kw = float(realtime_state.get("pv_power") or 0.0)

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

    def _infer_start_time(self, *forecasts: list[ParsedForecast]) -> datetime | None:
        for forecast in forecasts:
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

    def _parse_grid_limits(self, realtime_state: dict[str, Any], horizon: int) -> GridLimits:
        import_limit_raw = realtime_state.get("grid_import_limit_kw")
        export_limit_raw = realtime_state.get("grid_export_limit_kw")

        import_limit = self._coerce_float(import_limit_raw)
        export_limit = self._coerce_float(export_limit_raw)
        if import_limit is not None:
            import_limit = max(import_limit, 0.0)
        if export_limit is not None:
            export_limit = max(export_limit, 0.0)

        import_allowed_raw = realtime_state.get("import_allowed")
        import_allowed: list[bool]
        if isinstance(import_allowed_raw, list):
            allowed_list = cast(list[Any], import_allowed_raw)
            bools = [bool(x) for x in allowed_list]
            import_allowed = bools[:horizon] + [True] * max(0, horizon - len(bools))
        else:
            import_allowed = [True] * horizon

        return GridLimits(
            import_max_kw=import_limit,
            export_max_kw=export_limit,
            import_allowed=import_allowed,
        )

    def _parse_inverter_export_limit_kw(self, realtime_state: dict[str, Any]) -> float | None:
        raw = realtime_state.get("inverter_export_limit_kw")
        if raw is None:
            return self.default_inverter_export_limit_kw
        value = self._coerce_float(raw)
        if value is None:
            return self.default_inverter_export_limit_kw
        return max(value, 0.0)

    def _parse_inverter_efficiencies(
        self,
        realtime_state: dict[str, Any],
    ) -> tuple[float, float]:
        charge_raw = realtime_state.get("inverter_charge_efficiency")
        discharge_raw = realtime_state.get("inverter_discharge_efficiency")
        charge_eff = self._coerce_float(charge_raw)
        discharge_eff = self._coerce_float(discharge_raw)

        if charge_eff is None or charge_eff <= 0:
            charge_eff = self.default_inverter_charge_efficiency
        if discharge_eff is None or discharge_eff <= 0:
            discharge_eff = self.default_inverter_discharge_efficiency

        return min(charge_eff, 1.0), min(discharge_eff, 1.0)

    def _parse_price_limits(self, realtime_state: dict[str, Any]) -> tuple[float | None, float | None]:
        import_cap_raw = realtime_state.get("import_price_cap")
        export_floor_raw = realtime_state.get("export_price_floor")

        import_cap = self._coerce_float(import_cap_raw)
        export_floor = self._coerce_float(export_floor_raw)

        if import_cap is None:
            import_cap = self.default_import_price_cap
        if export_floor is None:
            export_floor = self.default_export_price_floor

        if import_cap is not None and import_cap < 0:
            import_cap = None
        if export_floor is not None and export_floor < 0:
            export_floor = None

        return import_cap, export_floor

    def _parse_batteries(self, realtime_state: dict[str, Any]) -> list[BatteryInputs]:
        raw = realtime_state.get("batteries")
        if not isinstance(raw, list):
            return []
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
            min_power_raw = entry_dict.get("min_power_kw", 0.0)
            switch_penalty_raw = entry_dict.get("switch_penalty", 0.0)
            try:
                min_power = float(min_power_raw)
            except (TypeError, ValueError):
                min_power = 0.0
            try:
                switch_penalty = float(switch_penalty_raw)
            except (TypeError, ValueError):
                switch_penalty = 0.0
            min_power = max(min_power, 0.0)
            switch_penalty = max(switch_penalty, 0.0)
            try:
                evs.append(
                    EvInputs(
                        name=str(entry_dict.get("name") or f"ev_{idx}"),
                        max_power_kw=float(entry_dict.get("max_power_kw", 0.0)),
                        min_power_kw=min_power,
                        availability=availability_list,
                        target_energy_kwh=(
                            float(target_energy) if target_energy is not None else None
                        ),
                        value_per_kwh=(
                            float(entry_dict["value_per_kwh"])
                            if "value_per_kwh" in entry_dict
                            else None
                        ),
                        switch_penalty=switch_penalty,
                    )
                )
            except (TypeError, ValueError):
                continue
        return evs

    def _coerce_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _missing_inputs(
        self,
        realtime_state: dict[str, Any],
        *,
        slots: list[HorizonSlot],
        grid_limits: GridLimits,
        batteries: list[BatteryInputs],
    ) -> list[str]:
        missing: list[str] = []
        if not realtime_state.get("import_price_forecast"):
            missing.append("import_price_forecast")
        if not realtime_state.get("export_price_forecast"):
            missing.append("export_price_forecast")
        if not realtime_state.get("pv_forecast"):
            missing.append("pv_forecast (kWh over interval)")
        if not batteries:
            missing.append("battery parameters (SOC, limits, efficiencies)")
        if grid_limits.import_max_kw is None:
            missing.append("grid_import_limit_kw")
        if grid_limits.export_max_kw is None:
            missing.append("grid_export_limit_kw")
        if not slots:
            missing.append("time discretization (forecast timeline)")
        return missing
