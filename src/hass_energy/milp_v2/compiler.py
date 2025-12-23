from __future__ import annotations

from datetime import datetime, timedelta

import pulp

from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.milp_v2.types import CompiledModel, HassLpProblem
from hass_energy.models.config import EmsConfig
from hass_energy.models.loads import LoadConfig
from hass_energy.models.plant import PlantConfig, TimeWindow


class MilpCompiler:
    """Phase 1: convert configuration + resolved values into a MILP model."""

    def compile(
        self,
        *,
        ems: EmsConfig,
        plant: PlantConfig,
        loads: list[LoadConfig],
        value_resolver: ValueResolver,
    ) -> CompiledModel:
        """Translate config and resolved values into a compiled model (grid-only for now)."""
        _ = loads
        grid = plant.grid
        slots = self._build_grid_slots(
            interval_duration=ems.interval_duration,
            num_intervals=ems.num_intervals,
            forbidden_periods=grid.import_forbidden_periods,
        )

        import_price = value_resolver.resolve(grid.realtime_price_import)
        export_price = value_resolver.resolve(grid.realtime_price_export)

        problem = HassLpProblem("milp_v2", pulp.LpMinimize)
        grid_import_vars: list[pulp.LpVariable] = []
        grid_export_vars: list[pulp.LpVariable] = []

        for idx, slot in enumerate(slots):
            import_limit = 0.0 if not slot["import_allowed"] else grid.max_import_kw
            grid_import = pulp.LpVariable(
                f"grid_import_kw_{idx}",
                lowBound=0,
                upBound=import_limit,
            )
            grid_export = pulp.LpVariable(
                f"grid_export_kw_{idx}",
                lowBound=0,
                upBound=grid.max_export_kw,
            )
            problem += grid_import <= import_limit, f"grid_import_max_{idx}"
            problem += grid_export <= grid.max_export_kw, f"grid_export_max_{idx}"
            if not slot["import_allowed"]:
                problem += grid_import == 0, f"grid_import_forbidden_{idx}"

            grid_import_vars.append(grid_import)
            grid_export_vars.append(grid_export)

        problem += pulp.lpSum(
            (
                (import_price * grid_import_vars[idx])
                - (export_price * grid_export_vars[idx])
            )
            * slot["duration_h"]
            for idx, slot in enumerate(slots)
        ), "objective"

        problem.hass_context = {
            "slots": slots,
            "grid_import_vars": grid_import_vars,
            "grid_export_vars": grid_export_vars,
            "import_price": import_price,
            "export_price": export_price,
        }
        return problem

    def _build_grid_slots(
        self,
        *,
        interval_duration: int,
        num_intervals: int,
        forbidden_periods: list[TimeWindow],
    ) -> list[dict[str, object]]:
        now = datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        minutes_since_midnight = now.hour * 60 + now.minute
        aligned_minutes = (minutes_since_midnight // interval_duration) * interval_duration
        start = midnight + timedelta(minutes=aligned_minutes)

        step = timedelta(minutes=interval_duration)
        total_slots = max(1, num_intervals)
        windows = [self._parse_time_window(window) for window in forbidden_periods]

        slots: list[dict[str, object]] = []
        for idx in range(total_slots):
            slot_start = start + step * idx
            slot_end = slot_start + step
            import_allowed = self._is_import_allowed(slot_start, windows)
            slots.append(
                {
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                    "duration_h": step.total_seconds() / 3600.0,
                    "import_allowed": import_allowed,
                }
            )
        return slots

    def _parse_time_window(self, window: TimeWindow) -> tuple[int, int]:
        start_h, start_m = (int(value) for value in window.start.split(":"))
        end_h, end_m = (int(value) for value in window.end.split(":"))
        return (start_h * 60 + start_m, end_h * 60 + end_m)

    def _is_import_allowed(
        self,
        slot_start: datetime,
        windows: list[tuple[int, int]],
    ) -> bool:
        if not windows:
            return True
        minute = slot_start.hour * 60 + slot_start.minute
        for start_minute, end_minute in windows:
            if self._is_time_in_window(minute, start_minute, end_minute):
                return False
        return True

    def _is_time_in_window(self, minute: int, start: int, end: int) -> bool:
        if start == end:
            return True
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end
