from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pulp

from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.inputs.models import AppliedForecastInput, AppliedInputRegistry
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.planning.horizon import build_horizon_shape
from energy_assistant.ems.planning.pricing import PriceSeriesBuilder
from energy_assistant.ems.planning.time_windows import TimeWindowMatcher
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.inputs.registry import ResolvedForecastInput, ResolvedInputRegistry
from energy_assistant.inputs.window import InputWindow
from energy_assistant.models.inputs import ForecastInputConfig, InputValueKind
from energy_assistant.models.plant import (
    GridComponentConfig,
    GridConstraintsConfig,
    InputReference,
    PriceBindingConfig,
    TimeWindow,
)


class _RecordingTimeWindowMatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[list[TimeWindow], datetime]] = []

    def matches(self, windows: Any, when: datetime) -> bool:
        self.calls.append((list(windows), when))
        return True


class _RecordingPriceSeriesBuilder:
    def __init__(self) -> None:
        self.build_calls: list[dict[str, Any]] = []

    def build_series(self, **kwargs: Any) -> SimpleNamespace:
        self.build_calls.append(kwargs)
        horizon = kwargs["horizon"]
        return SimpleNamespace(
            import_effective=[1.0] * horizon.num_intervals,
            export_effective=[2.0] * horizon.num_intervals,
        )

    def binding_bias_pct(self, **kwargs: Any) -> float:
        _ = kwargs
        return 0.0


class _RecordingPowerAligner(PowerForecastAligner):
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, float | None]] = []

    def align(
        self,
        horizon: Any,
        intervals: Any,
        *,
        first_slot_override: float | None = None,
    ) -> list[float]:
        self.calls.append((horizon, intervals, first_slot_override))
        return [3.0, 4.0]


class _RecordingPriceAligner(PriceForecastAligner):
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, float | None]] = []

    def align(
        self,
        horizon: Any,
        intervals: Any,
        *,
        first_slot_override: float | None = None,
    ) -> list[float]:
        self.calls.append((horizon, intervals, first_slot_override))
        return [5.0, 6.0]


class _FakeInputProvider:
    def __init__(self, resolved: ResolvedInputRegistry) -> None:
        self.resolved = resolved
        self.window: InputWindow | None = None

    def mark_for_hydration(self) -> None:
        return None

    def hydrate_all(self) -> None:
        return None

    def resolve_for_window(self, *, window: InputWindow) -> ResolvedInputRegistry:
        self.window = window
        return self.resolved

    def grid_price_watch_entity_ids(self) -> set[str]:
        return set()


class _FakeProblem:
    def __init__(self) -> None:
        self.status = 1
        self.objective = 0.0
        self.solve_calls: list[object] = []

    def solve(self, solver: object) -> None:
        self.solve_calls.append(solver)


class _FakeSystem:
    def __init__(self, problem: _FakeProblem) -> None:
        self.update_inputs_calls: list[tuple[object, object]] = []
        self.build_snapshot_calls: list[object] = []
        self.problem = problem

    def update_inputs(self, *, horizon: object, inputs: object) -> None:
        self.update_inputs_calls.append((horizon, inputs))

    def build_snapshot(self, *, horizon: object) -> tuple[object, object]:
        self.build_snapshot_calls.append(horizon)
        snapshot = SimpleNamespace(
            problem=self.problem,
            ctx=SimpleNamespace(horizon=horizon),
        )
        return snapshot, "solve-state"

    def build_component_plans(self, snapshot: object, *, solve_state: object) -> dict[str, object]:
        _ = snapshot, solve_state
        return {}


class _FakeInputApplicator:
    def apply_to_horizon(
        self,
        *,
        horizon: object,
        inputs: object,
    ) -> AppliedInputRegistry:
        _ = horizon, inputs
        return AppliedInputRegistry()


def _grid_config() -> GridComponentConfig:
    return GridComponentConfig(
        type="grid",
        connection="switchboard",
        constraints=GridConstraintsConfig(max_import_kw=10.0, max_export_kw=10.0),
        price_import=PriceBindingConfig(source=InputReference(source="grid_price_import")),
        price_export=PriceBindingConfig(source=InputReference(source="grid_price_export")),
        import_forbidden_periods=[TimeWindow(start="00:00", end="23:59")],
    )


def test_grid_component_uses_injected_dependencies() -> None:
    matcher = _RecordingTimeWindowMatcher()
    builder = _RecordingPriceSeriesBuilder()
    component = GridComponent(
        bus_id="switchboard",
        component_id="grid",
        grid=_grid_config(),
        time_window_matcher=cast(TimeWindowMatcher, matcher),
        price_series_builder=cast(PriceSeriesBuilder, builder),
    )
    horizon = build_horizon_shape(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )

    component.update_inputs(
        horizon=horizon,
        inputs=AppliedInputRegistry(
            forecasts={
                "grid_price_import": AppliedForecastInput(
                    key="grid_price_import",
                    kind=InputValueKind.PRICE,
                    series=[0.1, 0.2],
                ),
                "grid_price_export": AppliedForecastInput(
                    key="grid_price_export",
                    kind=InputValueKind.PRICE,
                    series=[0.3, 0.4],
                ),
            }
        ),
    )

    assert len(builder.build_calls) == 1
    assert len(matcher.calls) == horizon.num_intervals


def test_input_applicator_uses_injected_aligners() -> None:
    power_aligner = _RecordingPowerAligner()
    price_aligner = _RecordingPriceAligner()
    horizon = build_horizon_shape(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    applicator = EmsInputApplicator(
        input_configs={
            "price": ForecastInputConfig.model_validate(
                {
                    "type": "forecast",
                    "forecast": {
                        "type": "home_assistant",
                        "platform": "amber_express",
                        "entity": "sensor.price",
                    },
                }
            ),
            "power": ForecastInputConfig.model_validate(
                {
                    "type": "forecast",
                    "forecast": {
                        "type": "home_assistant",
                        "platform": "historical_average",
                        "entity": "sensor.power",
                        "history_days": 1,
                    },
                }
            ),
        },
        power_aligner=power_aligner,
        price_aligner=price_aligner,
    )
    resolved = ResolvedInputRegistry(
        forecasts={
            "price": ResolvedForecastInput(
                key="price",
                kind=InputValueKind.PRICE,
                points={
                    "2025-01-01T00:00:00+00:00": 1.0,
                    "2025-01-01T01:00:00+00:00": 2.0,
                },
                interval_minutes=60,
            ),
            "power": ResolvedForecastInput(
                key="power",
                kind=InputValueKind.POWER,
                points={
                    "2025-01-01T00:00:00+00:00": 3.0,
                    "2025-01-01T01:00:00+00:00": 4.0,
                },
                interval_minutes=60,
            ),
        }
    )

    applied = applicator.apply_to_horizon(horizon=horizon, inputs=resolved)

    assert applied.forecast("price", kind=InputValueKind.PRICE) == [5.0, 6.0]
    assert applied.forecast("power", kind=InputValueKind.POWER) == [3.0, 4.0]
    assert len(price_aligner.calls) == 1
    assert len(power_aligner.calls) == 1


def test_planner_uses_injected_system_factory() -> None:
    horizon_shape = build_horizon_shape(timestep_minutes=60, horizon_minutes=120)
    resolved = ResolvedInputRegistry()
    input_provider = _FakeInputProvider(resolved)
    problem = _FakeProblem()
    system = _FakeSystem(problem)
    system_factory = cast(
        EmsSystemFactory,
        SimpleNamespace(
        horizon_shape=horizon_shape,
        input_applicator=_FakeInputApplicator(),
        system=system,
        ),
    )
    planner = EmsMilpPlanner(
        input_provider=input_provider,
        system_factory=system_factory,
    )

    plan = planner.generate_ems_plan(now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC))

    assert input_provider.window is not None
    assert len(system.update_inputs_calls) == 1
    assert len(problem.solve_calls) == 1
    solver = problem.solve_calls[0]
    assert isinstance(solver, pulp.PULP_CBC_CMD)
    assert plan.status == "Optimal"
