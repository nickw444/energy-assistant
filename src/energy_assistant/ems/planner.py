from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import get_args

import pulp

from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import (
    EmsPlanOutput,
    EmsPlanStatus,
    EmsPlanTimings,
)
from energy_assistant.ems.system.state import EmsSystemSolveState
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.inputs.provider import EmsInputProvider
from energy_assistant.inputs.window import InputWindow

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmsBuiltSnapshot:
    solve_time: datetime
    build_seconds: float
    snapshot: ModelSnapshot
    system: EmsSystem
    solve_state: EmsSystemSolveState


@dataclass(frozen=True, slots=True)
class EmsSolvedRun:
    plan: EmsPlanOutput
    snapshot: ModelSnapshot
    system: EmsSystem
    solve_state: EmsSystemSolveState


class EmsMilpPlanner:
    def __init__(
        self,
        *,
        input_provider: EmsInputProvider,
        horizon_factory: HorizonFactory,
        input_applicator: EmsInputApplicator,
        system: EmsSystem,
    ) -> None:
        self._input_provider = input_provider
        self._horizon_factory = horizon_factory
        self._input_applicator = input_applicator
        self._system = system

    def mark_for_hydration(self) -> None:
        self._input_provider.mark_for_hydration()

    def hydrate_all(self) -> None:
        self._input_provider.hydrate_all()

    def generate_ems_run(
        self,
        *,
        now: datetime | None = None,
        solver_msg: bool = False,
    ) -> EmsSolvedRun:
        total_start = time.perf_counter()
        built = self.build_snapshot(now=now)

        solve_start = time.perf_counter()
        built.snapshot.problem.solve(pulp.PULP_CBC_CMD(msg=solver_msg))
        solve_seconds = time.perf_counter() - solve_start

        objective_value = _objective_value(built.snapshot.problem)
        status = _map_status(pulp.LpStatus.get(built.snapshot.problem.status, "Unknown"))
        components = built.system.build_component_plans(
            built.snapshot,
            solve_state=built.solve_state,
        )
        total_seconds = time.perf_counter() - total_start
        timings = EmsPlanTimings(
            build_seconds=built.build_seconds,
            solve_seconds=solve_seconds,
            total_seconds=total_seconds,
        )
        logger.info(
            "EMS plan timings: build=%.3fs solve=%.3fs total=%.3fs",
            built.build_seconds,
            solve_seconds,
            total_seconds,
        )
        plan = EmsPlanOutput(
            generated_at=built.solve_time.astimezone(UTC),
            status=status,
            objective_value=objective_value,
            timings=timings,
            components=components,
        )
        return EmsSolvedRun(
            plan=plan,
            snapshot=built.snapshot,
            system=built.system,
            solve_state=built.solve_state,
        )

    def build_snapshot(self, *, now: datetime | None = None) -> EmsBuiltSnapshot:
        solve_time = now or datetime.now().astimezone()
        if solve_time.tzinfo is None:
            solve_time = solve_time.astimezone()

        horizon_factory = self._horizon_factory
        horizon = horizon_factory.build(now=solve_time)
        schedule_info = _format_schedule(
            horizon_factory.high_res_timestep_minutes,
            horizon_factory.high_res_horizon_minutes,
            horizon_factory.timestep_minutes,
        )
        logger.info(
            (
                "EMS horizon: intervals=%s base_interval_minutes=%s "
                "total_minutes=%s start=%s schedule=%s"
            ),
            horizon.num_intervals,
            horizon.interval_minutes,
            horizon_factory.horizon_minutes,
            horizon.start.isoformat(),
            schedule_info,
        )

        build_start = time.perf_counter()
        resolved_inputs = self._input_provider.resolve_for_window(
            window=InputWindow(now=horizon.now, end=horizon.slots[-1].end)
        )
        applied_inputs = self._input_applicator.apply_to_horizon(
            horizon=horizon,
            inputs=resolved_inputs,
        )
        system = self._system
        snapshot, solve_state = system.build_snapshot(
            horizon=horizon,
            inputs=applied_inputs,
        )
        build_seconds = time.perf_counter() - build_start
        return EmsBuiltSnapshot(
            solve_time=solve_time,
            build_seconds=build_seconds,
            snapshot=snapshot,
            system=system,
            solve_state=solve_state,
        )


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
