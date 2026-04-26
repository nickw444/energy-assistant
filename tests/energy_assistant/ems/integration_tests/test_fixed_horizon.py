from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from energy_assistant.ems.components.context import GraphBuildContext
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.grid.price_bindings import PriceBindingApplicator
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.inputs.models import AppliedForecastInput, AppliedInputRegistry
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import LinearCost
from energy_assistant.inputs.provider import ResolverBackedInputProvider
from energy_assistant.inputs.window import InputWindow
from energy_assistant.lib.home_assistant import (
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
)
from energy_assistant.lib.source_resolver.hass_provider import HomeAssistantHistoryPayload
from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantHistoricalAveragePriceForecastSource,
)
from energy_assistant.lib.source_resolver.models import PriceForecastInterval
from energy_assistant.lib.source_resolver.sources import EntitySource
from energy_assistant.models.config import AppConfig
from energy_assistant.models.inputs import ForecastInputConfig, InputValueKind
from energy_assistant.models.plant import GridComponentConfig


class StubResolver:
    def __init__(self) -> None:
        self._values: dict[int, object] = {}
        self._entity_values: dict[str, object] = {}
        self._states: dict[str, HomeAssistantStateDict] = {}
        self._history: dict[str, list[HomeAssistantHistoryStateDict]] = {}

    def mark_for_hydration(self, value: object) -> None:
        _ = value

    def hydrate_all(self) -> None:
        return None

    def hydrate_history(self) -> None:
        return None

    def hydrate_states(self) -> None:
        return None

    def mark(self, source: EntitySource[object, object]) -> None:
        _ = source

    def set(self, source: object, value: object) -> None:
        self._values[id(source)] = value
        entity = getattr(source, "entity", None)
        if isinstance(entity, str):
            self._entity_values[entity] = value

    def set_state(self, entity_id: str, state: HomeAssistantStateDict) -> None:
        self._states[entity_id] = state

    def set_history(self, entity_id: str, history: list[HomeAssistantHistoryStateDict]) -> None:
        self._history[entity_id] = history

    def resolve[Q, R](self, source: EntitySource[Q, R]) -> R:
        if isinstance(source, HomeAssistantHistoricalAveragePriceForecastSource):
            payload = HomeAssistantHistoryPayload(
                history=self._history[source.entity],
                current_state=self._states[source.entity],
            )
            return cast(R, source.mapper(payload))
        entity = getattr(source, "entity", None)
        if isinstance(entity, str) and entity in self._entity_values:
            return cast(R, self._entity_values[entity])
        return cast(R, self._values[id(source)])


def _load_fixture_config() -> AppConfig:
    fixture_path = (
        Path(__file__).resolve().parents[4]
        / "tests"
        / "fixtures"
        / "ems"
        / "nwhass"
        / "config.yaml"
    )
    loaded_raw: Any = yaml.safe_load(fixture_path.read_text())
    assert isinstance(loaded_raw, dict)
    return AppConfig.model_validate(cast(dict[str, Any], loaded_raw))


def _minimal_grid_app_config(*, forecast_expansion: dict[str, int] | None) -> AppConfig:
    grid_price_import: dict[str, object] = {
        "type": "forecast",
        "forecast": {
            "type": "home_assistant",
            "platform": "amber_express",
            "entity": "sensor.price_import_forecast",
        },
        "realtime": {
            "type": "home_assistant",
            "entity": "sensor.price_import",
        },
    }
    grid_price_export: dict[str, object] = {
        "type": "forecast",
        "forecast": {
            "type": "home_assistant",
            "platform": "amber_express",
            "entity": "sensor.price_export_forecast",
        },
        "realtime": {
            "type": "home_assistant",
            "entity": "sensor.price_export",
        },
    }
    if forecast_expansion is not None:
        grid_price_import["forecast_expansion"] = forecast_expansion
        grid_price_export["forecast_expansion"] = forecast_expansion
    return AppConfig.model_validate(
        {
            "server": {
                "host": "127.0.0.1",
                "port": 6070,
                "data_dir": "./data",
            },
            "homeassistant": {
                "base_url": "https://hass.example.com",
                "token": "test-token",
            },
            "inputs": {
                "grid_price_import": grid_price_import,
                "grid_price_export": grid_price_export,
                "base_load_power": {
                    "type": "forecast",
                    "forecast": {
                        "type": "home_assistant",
                        "platform": "solcast",
                        "entities": [
                            "sensor.base_load_forecast_today",
                            "sensor.base_load_forecast_tomorrow",
                        ],
                    },
                },
            },
            "plant": {
                "switchboard": {"type": "switchboard"},
                "grid": {
                    "type": "grid",
                    "connection": "switchboard",
                    "constraints": {"max_import_kw": 10.0, "max_export_kw": 10.0},
                    "price_import": {"source": "inputs.grid_price_import"},
                    "price_export": {"source": "inputs.grid_price_export"},
                },
                "base_load": {
                    "type": "load",
                    "connection": "switchboard",
                    "name": "Base Load",
                    "power": "inputs.base_load_power",
                },
            },
        }
    )


def _price_intervals(
    *,
    start: datetime,
    interval_minutes: int,
    values: list[float],
) -> list[PriceForecastInterval]:
    intervals: list[PriceForecastInterval] = []
    cursor = start
    for value in values:
        end = cursor + timedelta(minutes=interval_minutes)
        intervals.append(PriceForecastInterval(start=cursor, end=end, value=value))
        cursor = end
    return intervals


def _history_point(*, ts: datetime, value: float) -> HomeAssistantHistoryStateDict:
    return {
        "last_updated": ts.isoformat(),
        "state": value,
    }


def _grid_component(config: AppConfig) -> GridComponentConfig:
    component = config.plant["grid"]
    assert isinstance(component, GridComponentConfig)
    return component


def _forecast_input(config: AppConfig, key: str) -> ForecastInputConfig:
    input_config = config.inputs[key]
    assert isinstance(input_config, ForecastInputConfig)
    return input_config


def _input_applicator(config: AppConfig) -> EmsInputApplicator:
    return EmsInputApplicator(
        input_configs=config.inputs,
        power_aligner=PowerForecastAligner(),
        price_aligner=PriceForecastAligner(),
    )


def _grid_component_instance(config: AppConfig) -> GridComponent:
    switchboard = SwitchboardComponent(component_id="switchboard")
    return GridComponent(
        component_id="grid",
        switchboard=switchboard,
        grid=_grid_component(config),
        time_window_matcher=TimeWindowMatcher(),
        price_binding_applicator=PriceBindingApplicator(),
    )


def _grid_build_ctx(component: GridComponent) -> GraphBuildContext:
    return GraphBuildContext(
        components={"switchboard": component.switchboard, component.id: component},
        solve_states=SolveStateStore(),
    )


def test_input_provider_uses_fixed_horizon_for_coverage_validation() -> None:
    config = _minimal_grid_app_config(forecast_expansion=None)
    resolver = StubResolver()
    provider = ResolverBackedInputProvider(app_config=config, resolver=resolver)
    applicator = _input_applicator(config)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=180).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    grid = _grid_component(config)
    short_intervals = _price_intervals(
        start=horizon.start,
        interval_minutes=60,
        values=[0.10, 0.11],
    )
    import_input = _forecast_input(config, grid.price_import.source.key)
    export_input = _forecast_input(config, grid.price_export.source.key)
    base_load_input = _forecast_input(config, "base_load_power")
    resolver.set(import_input.forecast, short_intervals)
    resolver.set(export_input.forecast, short_intervals)
    resolver.set(
        base_load_input.forecast,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[1.0, 1.0, 1.0]),
    )
    if base_load_input.realtime is not None:
        resolver.set(base_load_input.realtime, 1.0)
    assert import_input.realtime is not None
    assert export_input.realtime is not None
    resolver.set(import_input.realtime, 0.10)
    resolver.set(export_input.realtime, 0.05)

    resolved = provider.resolve_for_window(
        window=InputWindow(now=horizon.now, end=horizon.slots[-1].end)
    )
    try:
        applicator.apply_to_horizon(horizon=horizon, inputs=resolved)
    except ValueError as exc:
        assert "required=180 minutes" in str(exc)
        assert "available=120 minutes" in str(exc)
    else:
        raise AssertionError("Expected fixed-horizon coverage validation to fail")


def test_grid_rebinds_inputs_without_changing_component_ids() -> None:
    config = _load_fixture_config()
    component = _grid_component_instance(config)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )

    first_inputs = AppliedInputRegistry(
        forecasts={
            "grid_price_import": AppliedForecastInput(
                key="grid_price_import",
                kind=InputValueKind.PRICE,
                series=[0.20, 0.21],
            ),
            "grid_price_export": AppliedForecastInput(
                key="grid_price_export",
                kind=InputValueKind.PRICE,
                series=[0.05, 0.06],
            ),
        }
    )
    build_ctx = _grid_build_ctx(component)
    first_elements, first_run = component.create_graph_elements(
        horizon=horizon,
        inputs=first_inputs,
        build_ctx=build_ctx,
    )
    first_node = next(element for element in first_elements if isinstance(element, Node))
    first_connection = next(
        element for element in first_elements if isinstance(element, Connection)
    )
    first_costs = first_connection.policy(
        "grid_energy_cost",
        LinearCost,
    ).cost_b_to_a_per_kwh
    first_effective = first_run.price_import_effective

    second_inputs = AppliedInputRegistry(
        forecasts={
            "grid_price_import": AppliedForecastInput(
                key="grid_price_import",
                kind=InputValueKind.PRICE,
                series=[0.30, 0.31],
            ),
            "grid_price_export": AppliedForecastInput(
                key="grid_price_export",
                kind=InputValueKind.PRICE,
                series=[0.08, 0.09],
            ),
        }
    )
    second_elements, second_run = component.create_graph_elements(
        horizon=horizon,
        inputs=second_inputs,
        build_ctx=build_ctx,
    )
    second_node = next(element for element in second_elements if isinstance(element, Node))
    second_connection = next(
        element for element in second_elements if isinstance(element, Connection)
    )
    second_costs = second_connection.policy(
        "grid_energy_cost",
        LinearCost,
    ).cost_b_to_a_per_kwh
    second_effective = second_run.price_import_effective

    assert first_node.id == second_node.id == component.node_id
    assert first_connection.id == second_connection.id == f"{component.id}_link"
    assert first_costs != second_costs
    assert first_costs == first_effective
    assert second_costs == second_effective
    assert second_run.price_import_raw == [0.30, 0.31]


def test_input_provider_can_extend_short_price_forecast_from_history() -> None:
    config = _minimal_grid_app_config(
        forecast_expansion={"history_days": 2, "interval_duration": 60}
    )
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    resolver = StubResolver()
    provider = ResolverBackedInputProvider(app_config=config, resolver=resolver)
    applicator = _input_applicator(config)
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=180).build(now=now)

    grid = _grid_component(config)
    import_input = _forecast_input(config, grid.price_import.source.key)
    export_input = _forecast_input(config, grid.price_export.source.key)
    base_load_input = _forecast_input(config, "base_load_power")
    assert import_input.realtime is not None
    assert export_input.realtime is not None

    resolver.set(
        base_load_input.forecast,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[1.0, 1.0, 1.0]),
    )

    resolver.set(import_input.realtime, 0.20)
    resolver.set(export_input.realtime, 0.05)
    resolver.set(
        import_input.forecast,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.20]),
    )
    resolver.set(
        export_input.forecast,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.05]),
    )
    resolver.set_state(
        import_input.realtime.entity,
        {
            "entity_id": import_input.realtime.entity,
            "state": 0.20,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    resolver.set_state(
        export_input.realtime.entity,
        {
            "entity_id": export_input.realtime.entity,
            "state": 0.05,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    resolver.set_history(
        import_input.realtime.entity,
        [
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=30), value=0.30),
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=90), value=0.40),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=30), value=0.30),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=90), value=0.40),
        ],
    )
    resolver.set_history(
        export_input.realtime.entity,
        [
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=30), value=0.10),
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=90), value=0.15),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=30), value=0.10),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=90), value=0.15),
        ],
    )

    resolved = provider.resolve_for_window(
        window=InputWindow(now=horizon.now, end=horizon.slots[-1].end)
    )
    import_raw = resolved.forecast("grid_price_import", kind=InputValueKind.PRICE)
    export_raw = resolved.forecast("grid_price_export", kind=InputValueKind.PRICE)
    assert list(import_raw.points.values()) == [0.20]
    assert list(export_raw.points.values()) == [0.05]
    assert import_raw.realtime_value == 0.20
    assert export_raw.realtime_value == 0.05
    assert import_raw.extension_points is not None
    assert export_raw.extension_points is not None

    inputs = applicator.apply_to_horizon(horizon=horizon, inputs=resolved)
    assert inputs.forecast("grid_price_import", kind=InputValueKind.PRICE) == [0.20, 0.30, 0.40]
    assert inputs.forecast("grid_price_export", kind=InputValueKind.PRICE) == [0.05, 0.10, 0.15]


def test_price_extension_covers_unaligned_multi_resolution_horizon() -> None:
    config = _minimal_grid_app_config(
        forecast_expansion={"history_days": 3, "interval_duration": 30}
    )
    now = datetime(2025, 1, 1, 12, 38, tzinfo=UTC)
    resolver = StubResolver()
    provider = ResolverBackedInputProvider(app_config=config, resolver=resolver)
    applicator = _input_applicator(config)
    horizon = HorizonFactory(
        timestep_minutes=30,
        horizon_minutes=2880,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=120,
    ).build(now=now)

    grid = _grid_component(config)
    import_input = _forecast_input(config, grid.price_import.source.key)
    export_input = _forecast_input(config, grid.price_export.source.key)
    base_load_input = _forecast_input(config, "base_load_power")
    assert import_input.realtime is not None
    assert export_input.realtime is not None

    resolver.set(
        base_load_input.forecast,
        _price_intervals(
            start=horizon.start,
            interval_minutes=30,
            values=[1.0] * horizon.num_intervals,
        ),
    )

    resolver.set(import_input.realtime, 0.20)
    resolver.set(export_input.realtime, 0.05)
    resolver.set(
        import_input.forecast,
        _price_intervals(start=horizon.start, interval_minutes=30, values=[0.20]),
    )
    resolver.set(
        export_input.forecast,
        _price_intervals(start=horizon.start, interval_minutes=30, values=[0.05]),
    )
    resolver.set_state(
        import_input.realtime.entity,
        {
            "entity_id": import_input.realtime.entity,
            "state": 0.20,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    resolver.set_state(
        export_input.realtime.entity,
        {
            "entity_id": export_input.realtime.entity,
            "state": 0.05,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    repeated_import_history: list[HomeAssistantHistoryStateDict] = []
    repeated_export_history: list[HomeAssistantHistoryStateDict] = []
    forecast_start = datetime(2025, 1, 1, 12, 30, tzinfo=UTC)
    for day in (1, 2, 3):
        cursor = forecast_start - timedelta(days=day)
        while cursor < forecast_start - timedelta(days=day) + timedelta(hours=49):
            repeated_import_history.append(_history_point(ts=cursor, value=0.25))
            repeated_export_history.append(_history_point(ts=cursor, value=0.07))
            cursor += timedelta(minutes=30)
    resolver.set_history(import_input.realtime.entity, repeated_import_history)
    resolver.set_history(export_input.realtime.entity, repeated_export_history)

    resolved = provider.resolve_for_window(
        window=InputWindow(now=horizon.now, end=horizon.slots[-1].end)
    )
    raw_import = resolved.forecast("grid_price_import", kind=InputValueKind.PRICE)
    raw_export = resolved.forecast("grid_price_export", kind=InputValueKind.PRICE)
    assert raw_import.interval_minutes == 30
    assert raw_export.interval_minutes == 30
    assert raw_import.extension_points is not None
    assert raw_export.extension_points is not None

    inputs = applicator.apply_to_horizon(horizon=horizon, inputs=resolved)
    import_series = inputs.forecast("grid_price_import", kind=InputValueKind.PRICE)
    export_series = inputs.forecast("grid_price_export", kind=InputValueKind.PRICE)
    assert len(import_series) == horizon.num_intervals
    assert len(export_series) == horizon.num_intervals
    assert import_series[0] == 0.20
    assert export_series[0] == 0.05
    assert import_series[-1] == 0.25
    assert export_series[-1] == 0.07
