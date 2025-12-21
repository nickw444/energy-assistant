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
class GridLimits:
    import_max_kw: float | None
    export_max_kw: float | None
    import_allowed: list[bool]


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
        grid_limits = self._parse_grid_limits(realtime_state, len(slots))

        problem, variables = self._build_core_model(slots, grid_limits)

        status: int = problem.solve(self._solver)  # type: ignore[no-untyped-call]
        objective_value = cast(
            float | None, pulp.value(problem.objective)  # type: ignore[arg-type, no-untyped-call]
        )
        objective = float(objective_value) if objective_value is not None else None

        plan_slots: list[dict[str, Any]] = []
        total_import_kwh = 0.0
        total_export_kwh = 0.0
        total_cost = 0.0

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
                    "battery": {},
                    "ev": {},
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
                "forecast_window_hours": config.forecast_window_hours,
                "realtime_sample_size": len(realtime_state),
                "history_sample_size": len(history),
                "compiled": compiled.metadata,
                "features": [
                    "core_balance",
                    "pv_curtailment",
                    "grid_limits" if grid_limits.import_max_kw or grid_limits.export_max_kw else "grid_unlimited",
                ],
                "missing_inputs": self._missing_inputs(
                    realtime_state,
                    slots=slots,
                    grid_limits=grid_limits,
                ),
            },
        }

    def _build_core_model(
        self,
        slots: list[HorizonSlot],
        grid_limits: GridLimits,
    ) -> tuple[pulp.LpProblem, CoreVariables]:
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

            problem += (
                slot["pv_kw"] - pv_curt[idx] + grid_import[idx]
                == slot["load_kw"] + grid_export[idx]
            ), f"power_balance_{idx}"

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

        problem += pulp.lpSum(objective_terms)

        return problem, CoreVariables(
            grid_import=grid_import,
            grid_export=grid_export,
            pv_curt=pv_curt,
        )

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
        horizon_hours = max(int(config.forecast_window_hours), 1)
        step_minutes = 5
        step = timedelta(minutes=step_minutes)
        total_steps = max(1, int(horizon_hours * 60 / step_minutes))

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
    ) -> list[str]:
        missing: list[str] = []
        if not realtime_state.get("import_price_forecast"):
            missing.append("import_price_forecast")
        if not realtime_state.get("export_price_forecast"):
            missing.append("export_price_forecast")
        if not realtime_state.get("pv_forecast"):
            missing.append("pv_forecast (kWh over interval)")
        if grid_limits.import_max_kw is None:
            missing.append("grid_import_limit_kw")
        if grid_limits.export_max_kw is None:
            missing.append("grid_export_limit_kw")
        if not slots:
            missing.append("time discretization (forecast timeline)")
        return missing
