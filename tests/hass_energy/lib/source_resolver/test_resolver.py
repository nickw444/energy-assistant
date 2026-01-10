from __future__ import annotations

import pytest
from pydantic import BaseModel

from hass_energy.lib.home_assistant import (
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
)
from hass_energy.lib.source_resolver.hass_provider import (
    HassDataProvider,
    HomeAssistantHistoryPayload,
    ServiceCallRequest,
    service_call_key,
)
from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantCurrencyEntitySource,
    HomeAssistantEntitySource,
    HomeAssistantHistoricalAverageForecastSource,
    HomeAssistantHistoryEntitySource,
    HomeAssistantMultiEntitySource,
    HomeAssistantPowerKwEntitySource,
    HomeAssistantSolcastForecastSource,
)
from hass_energy.lib.source_resolver.resolver import ValueResolverImpl
from hass_energy.lib.source_resolver.sources import EntitySource


class _StubProvider(HassDataProvider):
    def __init__(self) -> None:
        self.marked_entities: set[str] = set()
        self.marked_history_entities: dict[str, int] = {}
        self.states: dict[str, HomeAssistantStateDict] = {}
        self.history: dict[str, list[HomeAssistantHistoryStateDict]] = {}
        self.marked_service_calls: dict[str, ServiceCallRequest] = {}
        self.service_calls: dict[str, object] = {}
        self.fetch_states_calls = 0
        self.fetch_history_calls = 0
        self.fetch_service_calls_calls = 0

    def mark(self, entity_id: str) -> None:
        self.marked_entities.add(entity_id)

    def mark_history(self, entity_id: str, history_days: int) -> None:
        self.marked_history_entities[entity_id] = history_days

    def fetch(self) -> None:
        self.fetch_states()
        self.fetch_history()
        self.fetch_service_calls()

    def mark_service_call(self, request: ServiceCallRequest) -> None:
        self.marked_service_calls[service_call_key(request)] = request

    def get(self, entity_id: str) -> HomeAssistantStateDict:
        return self.states[entity_id]

    def get_history(self, entity_id: str) -> list[HomeAssistantHistoryStateDict]:
        return self.history[entity_id]

    def get_service_call(self, request: ServiceCallRequest) -> object:
        return self.service_calls[service_call_key(request)]

    def fetch_states(self) -> None:
        self.fetch_states_calls += 1

    def fetch_history(self) -> None:
        self.fetch_history_calls += 1

    def fetch_service_calls(self) -> None:
        self.fetch_service_calls_calls += 1


class _DemoConfig(BaseModel):
    power: HomeAssistantPowerKwEntitySource
    price: HomeAssistantCurrencyEntitySource
    pv: HomeAssistantSolcastForecastSource
    history: HomeAssistantHistoricalAverageForecastSource
    nested: list[HomeAssistantPowerKwEntitySource]
    mapping: dict[str, HomeAssistantPowerKwEntitySource]


class _TupleConfig(BaseModel):
    primary: HomeAssistantPowerKwEntitySource | None
    sources: tuple[HomeAssistantPowerKwEntitySource, HomeAssistantPowerKwEntitySource]


class _SimpleEntitySource(HomeAssistantEntitySource[int]):
    def mapper(self, state: HomeAssistantStateDict) -> int:
        return 42


class _SimpleHistorySource(HomeAssistantHistoryEntitySource[int]):
    def mapper(self, state: HomeAssistantHistoryPayload) -> int:
        return len(state.history)


class _SimpleMultiSource(HomeAssistantMultiEntitySource[int]):
    def mapper(self, state: list[HomeAssistantStateDict]) -> int:
        return len(state)


class _ExplodingHistorySource(HomeAssistantHistoryEntitySource[int]):
    def mapper(self, state: HomeAssistantHistoryPayload) -> int:
        raise RuntimeError("boom")


class _ExplodingMultiSource(HomeAssistantMultiEntitySource[int]):
    def mapper(self, state: list[HomeAssistantStateDict]) -> int:
        raise RuntimeError("boom")


class _UnsupportedSource(EntitySource[int, int]):
    def mapper(self, state: int) -> int:
        return state


def test_mark_for_hydration_walks_nested_config() -> None:
    provider = _StubProvider()
    resolver = ValueResolverImpl(provider)
    config = _DemoConfig(
        power=HomeAssistantPowerKwEntitySource(
            type="home_assistant",
            entity="sensor.load",
        ),
        price=HomeAssistantCurrencyEntitySource(
            type="home_assistant",
            entity="sensor.price",
        ),
        pv=HomeAssistantSolcastForecastSource(
            type="home_assistant",
            platform="solcast",
            entities=["sensor.pv_a", "sensor.pv_b"],
        ),
        history=HomeAssistantHistoricalAverageForecastSource(
            type="home_assistant",
            platform="historical_average",
            entity="sensor.load_history",
            history_days=2,
            unit="kW",
            interval_duration=15,
        ),
        nested=[
            HomeAssistantPowerKwEntitySource(
                type="home_assistant",
                entity="sensor.nested",
            )
        ],
        mapping={
            "extra": HomeAssistantPowerKwEntitySource(
                type="home_assistant",
                entity="sensor.extra",
            )
        },
    )

    resolver.mark_for_hydration(config)

    assert provider.marked_entities == {
        "sensor.load",
        "sensor.price",
        "sensor.load_history",
        "sensor.pv_a",
        "sensor.pv_b",
        "sensor.nested",
        "sensor.extra",
    }
    assert provider.marked_history_entities == {"sensor.load_history": 2}


def test_mark_for_hydration_handles_tuples_and_nones() -> None:
    provider = _StubProvider()
    resolver = ValueResolverImpl(provider)
    config = _TupleConfig(
        primary=None,
        sources=(
            HomeAssistantPowerKwEntitySource(
                type="home_assistant",
                entity="sensor.a",
            ),
            HomeAssistantPowerKwEntitySource(
                type="home_assistant",
                entity="sensor.b",
            ),
        ),
    )

    resolver.mark_for_hydration(config)

    assert provider.marked_entities == {"sensor.a", "sensor.b"}


def test_mark_for_hydration_marks_realtime_when_window_configured() -> None:
    provider = _StubProvider()
    resolver = ValueResolverImpl(provider)
    config = _DemoConfig(
        power=HomeAssistantPowerKwEntitySource(
            type="home_assistant",
            entity="sensor.load",
        ),
        price=HomeAssistantCurrencyEntitySource(
            type="home_assistant",
            entity="sensor.price",
        ),
        pv=HomeAssistantSolcastForecastSource(
            type="home_assistant",
            platform="solcast",
            entities=["sensor.pv_a", "sensor.pv_b"],
        ),
        history=HomeAssistantHistoricalAverageForecastSource(
            type="home_assistant",
            platform="historical_average",
            entity="sensor.load_history",
            history_days=2,
            unit="kW",
            interval_duration=15,
            realtime_window_minutes=30,
        ),
        nested=[
            HomeAssistantPowerKwEntitySource(
                type="home_assistant",
                entity="sensor.nested",
            )
        ],
        mapping={
            "extra": HomeAssistantPowerKwEntitySource(
                type="home_assistant",
                entity="sensor.extra",
            )
        },
    )

    resolver.mark_for_hydration(config)

    assert "sensor.load_history" in provider.marked_entities
    assert provider.marked_history_entities == {"sensor.load_history": 2}


def test_hydration_calls_provider_methods() -> None:
    provider = _StubProvider()
    resolver = ValueResolverImpl(provider)

    resolver.hydrate_all()
    resolver.hydrate_states()
    resolver.hydrate_history()

    assert provider.fetch_states_calls == 2
    assert provider.fetch_history_calls == 2
    assert provider.fetch_service_calls_calls == 1


def test_resolve_entity_source() -> None:
    provider = _StubProvider()
    provider.states["sensor.simple"] = {
        "entity_id": "sensor.simple",
        "state": 1,
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    resolver = ValueResolverImpl(provider)
    source = _SimpleEntitySource(
        type="home_assistant",
        entity="sensor.simple",
    )

    assert resolver.resolve(source) == 42


def test_resolve_wraps_mapper_errors() -> None:
    provider = _StubProvider()
    provider.states["sensor.load"] = {
        "entity_id": "sensor.load",
        "state": None,
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    resolver = ValueResolverImpl(provider)
    source = HomeAssistantPowerKwEntitySource(
        type="home_assistant",
        entity="sensor.load",
    )

    with pytest.raises(ValueError, match="sensor.load"):
        resolver.resolve(source)


def test_resolve_history_source() -> None:
    provider = _StubProvider()
    provider.history["sensor.history"] = [{"state": "ok"}]
    provider.states["sensor.history"] = {
        "entity_id": "sensor.history",
        "state": "ok",
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    resolver = ValueResolverImpl(provider)
    source = _SimpleHistorySource(
        type="home_assistant",
        entity="sensor.history",
        history_days=1,
    )

    assert resolver.resolve(source) == 1


def test_resolve_multi_entity_source() -> None:
    provider = _StubProvider()
    provider.states["sensor.a"] = {
        "entity_id": "sensor.a",
        "state": 1,
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    provider.states["sensor.b"] = {
        "entity_id": "sensor.b",
        "state": 2,
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    resolver = ValueResolverImpl(provider)
    source = _SimpleMultiSource(
        type="home_assistant",
        entities=["sensor.a", "sensor.b"],
    )

    assert resolver.resolve(source) == 2


def test_resolve_history_wraps_mapper_errors() -> None:
    provider = _StubProvider()
    provider.history["sensor.history"] = [{"state": "ok"}]
    provider.states["sensor.history"] = {
        "entity_id": "sensor.history",
        "state": "ok",
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    resolver = ValueResolverImpl(provider)
    source = _ExplodingHistorySource(
        type="home_assistant",
        entity="sensor.history",
        history_days=1,
    )

    with pytest.raises(ValueError, match="sensor.history"):
        resolver.resolve(source)


def test_resolve_multi_entity_wraps_mapper_errors() -> None:
    provider = _StubProvider()
    provider.states["sensor.a"] = {
        "entity_id": "sensor.a",
        "state": 1,
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    provider.states["sensor.b"] = {
        "entity_id": "sensor.b",
        "state": 2,
        "attributes": {},
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }
    resolver = ValueResolverImpl(provider)
    source = _ExplodingMultiSource(
        type="home_assistant",
        entities=["sensor.a", "sensor.b"],
    )

    with pytest.raises(ValueError, match="entities"):
        resolver.resolve(source)


def test_resolve_unsupported_source_type() -> None:
    provider = _StubProvider()
    resolver = ValueResolverImpl(provider)
    source = _UnsupportedSource()

    with pytest.raises(ValueError, match="Unsupported source type"):
        resolver.resolve(source)
