from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import get_args

import pulp

from energy_assistant.ems.input_provider import EmsInputProvider, ResolverBackedInputProvider
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
    def __init__(
        self,
        app_config: AppConfig,
        *,
        input_provider: EmsInputProvider | None = None,
        resolver: ValueResolver | None = None,
    ) -> None:
        self._app_config = app_config
        if input_provider is not None:
            self._input_provider = input_provider
        elif resolver is not None:
            self._input_provider = ResolverBackedInputProvider(
                app_config=app_config,
                resolver=resolver,
            )
        else:
            raise ValueError("EmsMilpPlanner requires an input_provider or resolver")
        self._last_timings: EmsPlanTimings | None = None
        self._system_factory = EmsSystemFactory(app_config)

    def mark_for_hydration(self) -> None:
        self._input_provider.mark_for_hydration()

    def hydrate_all(self) -> None:
        self._input_provider.hydrate_all()

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

        horizon_shape = self._system_factory.horizon_shape
        horizon = horizon_shape.build(now=solve_time)
        schedule_info = _format_schedule(
            horizon_shape.high_res_timestep_minutes,
            horizon_shape.high_res_horizon_minutes,
            horizon_shape.timestep_minutes,
        )
        logger.info(
            (
                "EMS horizon: intervals=%s base_interval_minutes=%s "
                "total_minutes=%s start=%s schedule=%s"
            ),
            horizon.num_intervals,
            horizon.interval_minutes,
            horizon_shape.horizon_minutes,
            horizon.start.isoformat(),
            schedule_info,
        )
        build_start = time.perf_counter()
        resolved_inputs = self._input_provider.resolve_for_horizon(horizon=horizon)
        applied_inputs = self._system_factory.input_applicator.apply_to_horizon(
            horizon=horizon,
            inputs=resolved_inputs,
        )
        system = self._system_factory.system
        system.update_inputs(horizon=horizon, inputs=applied_inputs)
        snapshot = system.build_snapshot(horizon=horizon)
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
