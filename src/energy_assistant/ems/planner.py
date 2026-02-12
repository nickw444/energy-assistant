from __future__ import annotations

import logging
import math
import time
from datetime import UTC, datetime
from typing import get_args

import pulp

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.models import (
    EmsPlanOutput,
    EmsPlanStatus,
    EmsPlanTimings,
)
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import AppConfig

logger = logging.getLogger(__name__)


class EmsMilpPlanner:
    def __init__(self, app_config: AppConfig, *, resolver: ValueResolver) -> None:
        self._app_config = app_config
        self._resolver = resolver
        self._last_timings: EmsPlanTimings | None = None
        self._system_factory = EmsSystemFactory(app_config, resolver=resolver)

    def mark_for_hydration(self) -> None:
        self._system_factory.mark_for_hydration()

    def generate_ems_plan(
        self,
        *,
        now: datetime | None = None,
        solver_msg: bool = False,
    ) -> EmsPlanOutput:
        total_start = time.perf_counter()
        solve_time = now or datetime.now().astimezone()
        if solve_time.tzinfo is None:
            solve_time = solve_time.astimezone()

        high_res_timestep = self._app_config.ems.high_res_timestep_minutes
        high_res_horizon = self._app_config.ems.high_res_horizon_minutes
        # Base interval used to size the forecast horizon and align forecasts into slots.
        base_interval_minutes = high_res_timestep or self._app_config.ems.timestep_minutes
        horizon_intervals = self._validate_min_horizon_intervals(
            self._system_factory.forecast_coverage_intervals(
                now=solve_time,
                interval_minutes=base_interval_minutes,
            ),
            base_interval_minutes,
        )
        total_minutes = horizon_intervals * base_interval_minutes
        horizon = build_horizon(
            now=solve_time,
            timestep_minutes=self._app_config.ems.timestep_minutes,
            num_intervals=horizon_intervals,
            high_res_timestep_minutes=high_res_timestep,
            high_res_horizon_minutes=high_res_horizon,
            total_minutes=total_minutes,
        )
        schedule_info = _format_schedule(
            high_res_timestep,
            high_res_horizon,
            self._app_config.ems.timestep_minutes,
        )
        horizon_msg = (
            "EMS horizon: intervals=%s base_interval_minutes=%s total_minutes=%s "
            "start=%s schedule=%s"
        )
        logger.info(
            horizon_msg,
            horizon.num_intervals,
            base_interval_minutes,
            total_minutes,
            horizon.start.isoformat(),
            schedule_info,
        )
        build_start = time.perf_counter()
        system = self._system_factory.build_system_for_run()
        snapshot = system.build_snapshot(horizon=horizon, resolver=self._resolver)
        build_seconds = time.perf_counter() - build_start

        solve_start = time.perf_counter()
        snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=solver_msg))
        solve_seconds = time.perf_counter() - solve_start

        objective_value = _objective_value(snapshot.problem)
        status = _map_status(pulp.LpStatus.get(snapshot.problem.status, "Unknown"))
        timesteps = system.build_timestep_plans(snapshot)
        total_seconds = time.perf_counter() - total_start
        timings = EmsPlanTimings(
            build_seconds=build_seconds,
            solve_seconds=solve_seconds,
            total_seconds=total_seconds,
        )
        self._last_timings = timings
        logger.info(
            "EMS plan timings: build=%.3fs solve=%.3fs total=%.3fs",
            build_seconds,
            solve_seconds,
            total_seconds,
        )
        return EmsPlanOutput(
            generated_at=solve_time.astimezone(UTC),
            status=status,
            objective_value=objective_value,
            timings=timings,
            timesteps=timesteps,
        )

    @property
    def last_timings(self) -> EmsPlanTimings | None:
        return self._last_timings

    def _validate_min_horizon_intervals(
        self,
        min_coverage_intervals: int,
        base_interval_minutes: int,
    ) -> int:
        min_minutes = self._app_config.ems.min_horizon_minutes
        min_intervals = math.ceil(min_minutes / base_interval_minutes)
        if min_coverage_intervals < min_intervals:
            coverage_minutes = min_coverage_intervals * base_interval_minutes
            raise ValueError(
                "Shortest forecast horizon "
                f"({min_coverage_intervals} intervals, {coverage_minutes} minutes) "
                f"is below min_horizon_minutes={min_minutes}"
            )
        return min_coverage_intervals


_VALID_STATUSES: frozenset[str] = frozenset(get_args(EmsPlanStatus))


def _map_status(status_text: str) -> EmsPlanStatus:
    if status_text in _VALID_STATUSES:
        return status_text  # type: ignore[return-value]
    return "Unknown"


def _objective_value(problem: pulp.LpProblem) -> float | None:
    v = pulp.value(problem.objective)
    if v is None:
        return None
    return float(v)


def _format_schedule(
    high_res_interval: int | None,
    high_res_horizon: int | None,
    timestep_minutes: int,
) -> str:
    if high_res_interval is None or high_res_horizon is None:
        return f"{timestep_minutes}m/rest"
    return f"{high_res_interval}m/{high_res_horizon}m, {timestep_minutes}m/rest"
