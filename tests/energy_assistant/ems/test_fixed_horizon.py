from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.horizon import build_horizon_shape
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import LinearCost
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
from energy_assistant.models.plant import GridPriceForecastExtensionConfig


class StubResolver:
    def __init__(self) -> None:
        self._values: dict[int, object] = {}
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

    def set(self, source: object, value: object) -> None:
        self._values[id(source)] = value

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
        return cast(R, self._values[id(source)])

    def mark(self, source: EntitySource[object, object]) -> None:
        _ = source


def _load_fixture_config() -> AppConfig:
    fixture_path = Path("tests/fixtures/ems/nwhass/ems_config.yaml")
    loaded_raw: Any = yaml.safe_load(fixture_path.read_text())
    assert isinstance(loaded_raw, dict)
    return AppConfig.model_validate(cast(dict[str, Any], loaded_raw))


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


def test_grid_validate_forecast_coverage_uses_fixed_horizon() -> None:
    config = _load_fixture_config()
    resolver = StubResolver()
    component = GridComponent(bus_id="ac_bus", grid=config.plant.grid)
    horizon = build_horizon_shape(timestep_minutes=60, horizon_minutes=180).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    short_intervals = _price_intervals(
        start=horizon.start,
        interval_minutes=60,
        values=[0.10, 0.11],
    )
    resolver.set(config.plant.grid.price_import_forecast, short_intervals)
    resolver.set(config.plant.grid.price_export_forecast, short_intervals)

    try:
        component.validate_forecast_coverage(horizon=horizon, resolver=resolver)
    except ValueError as exc:
        assert "required=180 minutes" in str(exc)
        assert "available=120 minutes" in str(exc)
    else:
        raise AssertionError("Expected fixed-horizon coverage validation to fail")


def test_grid_rebinds_inputs_without_changing_topology_ids() -> None:
    config = _load_fixture_config()
    resolver = StubResolver()
    component = GridComponent(bus_id="ac_bus", grid=config.plant.grid)
    horizon = build_horizon_shape(timestep_minutes=60, horizon_minutes=120).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )

    import_source = config.plant.grid.price_import_forecast
    export_source = config.plant.grid.price_export_forecast
    realtime_import = config.plant.grid.realtime_price_import
    realtime_export = config.plant.grid.realtime_price_export

    resolver.set(realtime_import, 0.20)
    resolver.set(realtime_export, 0.05)
    resolver.set(
        import_source,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.20, 0.21]),
    )
    resolver.set(
        export_source,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.05, 0.06]),
    )
    component.update_inputs(horizon=horizon, resolver=resolver)
    first_elements = component.graph_elements(horizon=horizon)
    first_node = next(element for element in first_elements if isinstance(element, Node))
    first_connection = next(
        element for element in first_elements if isinstance(element, Connection)
    )
    first_costs = first_connection.policy(
        "grid_energy_cost",
        LinearCost,
    ).cost_b_to_a_per_kwh
    first_effective = component.latest_price_import_effective()

    resolver.set(realtime_import, 0.30)
    resolver.set(realtime_export, 0.08)
    resolver.set(
        import_source,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.30, 0.31]),
    )
    resolver.set(
        export_source,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.08, 0.09]),
    )
    component.update_inputs(horizon=horizon, resolver=resolver)
    second_elements = component.graph_elements(horizon=horizon)
    second_node = next(element for element in second_elements if isinstance(element, Node))
    second_connection = next(
        element for element in second_elements if isinstance(element, Connection)
    )
    second_costs = second_connection.policy(
        "grid_energy_cost",
        LinearCost,
    ).cost_b_to_a_per_kwh
    second_effective = component.latest_price_import_effective()

    assert first_node.id == second_node.id == component.node_id
    assert first_connection.id == second_connection.id == component.connection_id
    assert first_costs != second_costs
    assert first_costs == first_effective
    assert second_costs == second_effective
    assert component.latest_price_import_raw() == [0.30, 0.31]


def test_grid_can_extend_short_price_forecast_from_history() -> None:
    config = _load_fixture_config()
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    grid_cfg = config.plant.grid.model_copy(
        update={
            "price_forecast_extension": GridPriceForecastExtensionConfig(
                history_days=2,
                interval_duration=60,
            )
        }
    )
    resolver = StubResolver()
    component = GridComponent(bus_id="ac_bus", grid=grid_cfg)
    horizon = build_horizon_shape(timestep_minutes=60, horizon_minutes=180).build(
        now=now
    )

    resolver.set(grid_cfg.realtime_price_import, 0.20)
    resolver.set(grid_cfg.realtime_price_export, 0.05)
    resolver.set(
        grid_cfg.price_import_forecast,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.20]),
    )
    resolver.set(
        grid_cfg.price_export_forecast,
        _price_intervals(start=horizon.start, interval_minutes=60, values=[0.05]),
    )
    resolver.set_state(
        grid_cfg.realtime_price_import.entity,
        {
            "entity_id": grid_cfg.realtime_price_import.entity,
            "state": 0.20,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    resolver.set_state(
        grid_cfg.realtime_price_export.entity,
        {
            "entity_id": grid_cfg.realtime_price_export.entity,
            "state": 0.05,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    resolver.set_history(
        grid_cfg.realtime_price_import.entity,
        [
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=30), value=0.30),
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=90), value=0.40),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=30), value=0.30),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=90), value=0.40),
        ],
    )
    resolver.set_history(
        grid_cfg.realtime_price_export.entity,
        [
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=30), value=0.10),
            _history_point(ts=now - timedelta(days=2) + timedelta(minutes=90), value=0.15),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=30), value=0.10),
            _history_point(ts=now - timedelta(days=1) + timedelta(minutes=90), value=0.15),
        ],
    )

    component.validate_forecast_coverage(horizon=horizon, resolver=resolver)
    component.update_inputs(horizon=horizon, resolver=resolver)

    assert component.latest_price_import_raw() == [0.20, 0.30, 0.40]
    assert component.latest_price_export_raw() == [0.05, 0.10, 0.15]


def test_grid_price_extension_covers_unaligned_multi_resolution_horizon() -> None:
    config = _load_fixture_config()
    now = datetime(2025, 1, 1, 12, 38, tzinfo=UTC)
    grid_cfg = config.plant.grid.model_copy(
        update={
            "price_forecast_extension": GridPriceForecastExtensionConfig(
                history_days=2,
                interval_duration=30,
            )
        }
    )
    resolver = StubResolver()
    component = GridComponent(bus_id="ac_bus", grid=grid_cfg)
    horizon = build_horizon_shape(
        timestep_minutes=30,
        horizon_minutes=2880,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=120,
    ).build(now=now)

    forecast_start = datetime(2025, 1, 1, 12, 30, tzinfo=UTC)
    resolver.set(grid_cfg.realtime_price_import, 0.20)
    resolver.set(grid_cfg.realtime_price_export, 0.05)
    resolver.set(
        grid_cfg.price_import_forecast,
        _price_intervals(start=forecast_start, interval_minutes=30, values=[0.20]),
    )
    resolver.set(
        grid_cfg.price_export_forecast,
        _price_intervals(start=forecast_start, interval_minutes=30, values=[0.05]),
    )
    resolver.set_state(
        grid_cfg.realtime_price_import.entity,
        {
            "entity_id": grid_cfg.realtime_price_import.entity,
            "state": 0.20,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    resolver.set_state(
        grid_cfg.realtime_price_export.entity,
        {
            "entity_id": grid_cfg.realtime_price_export.entity,
            "state": 0.05,
            "attributes": {},
            "last_changed": now.isoformat(),
            "last_reported": now.isoformat(),
            "last_updated": now.isoformat(),
        },
    )
    resolver.set_history(
        grid_cfg.realtime_price_import.entity,
        [
            _history_point(ts=datetime(2024, 12, 30, 12, 30, tzinfo=UTC), value=0.30),
            _history_point(ts=datetime(2024, 12, 30, 13, 0, tzinfo=UTC), value=0.40),
            _history_point(ts=datetime(2024, 12, 31, 12, 30, tzinfo=UTC), value=0.30),
            _history_point(ts=datetime(2024, 12, 31, 13, 0, tzinfo=UTC), value=0.40),
        ],
    )
    resolver.set_history(
        grid_cfg.realtime_price_export.entity,
        [
            _history_point(ts=datetime(2024, 12, 30, 12, 30, tzinfo=UTC), value=0.10),
            _history_point(ts=datetime(2024, 12, 30, 13, 0, tzinfo=UTC), value=0.15),
            _history_point(ts=datetime(2024, 12, 31, 12, 30, tzinfo=UTC), value=0.10),
            _history_point(ts=datetime(2024, 12, 31, 13, 0, tzinfo=UTC), value=0.15),
        ],
    )

    component.validate_forecast_coverage(horizon=horizon, resolver=resolver)
    component.update_inputs(horizon=horizon, resolver=resolver)

    assert len(component.latest_price_import_raw()) == horizon.num_intervals
    assert len(component.latest_price_export_raw()) == horizon.num_intervals
