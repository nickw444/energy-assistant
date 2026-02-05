from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pytest

import energy_assistant.lib.source_resolver.hass_source as hass_source
from energy_assistant.lib.home_assistant import (
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
)
from energy_assistant.lib.source_resolver.hass_provider import HomeAssistantHistoryPayload
from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantHistoricalAverageForecastSource,
)


def _freeze_hass_source_time(monkeypatch: pytest.MonkeyPatch, frozen: datetime) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen
            if frozen.tzinfo is None:
                return frozen.replace(tzinfo=tz)
            return frozen.astimezone(tz)

    monkeypatch.setattr(hass_source.datetime, "datetime", FrozenDateTime)


def test_historical_average_wraps_for_longer_horizon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2025, 1, 2, 6, 30, tzinfo=UTC)
    _freeze_hass_source_time(monkeypatch, now)

    source = HomeAssistantHistoricalAverageForecastSource(
        type="home_assistant",
        platform="historical_average",
        entity="load_forecast",
        history_days=2,
        unit="kW",
        interval_duration=60,
        forecast_horizon_hours=48,
    )

    history: list[HomeAssistantHistoryStateDict] = [
        {
            "last_updated": datetime(2025, 1, 1, 0, 0, tzinfo=UTC).isoformat(),
            "state": 1.0,
        },
        {
            "last_updated": datetime(2025, 1, 1, 12, 0, tzinfo=UTC).isoformat(),
            "state": 2.0,
        },
        {
            "last_updated": datetime(2025, 1, 2, 0, 0, tzinfo=UTC).isoformat(),
            "state": 1.0,
        },
    ]
    current_state: HomeAssistantStateDict = {
        "entity_id": "sensor.load",
        "state": 1.0,
        "attributes": {"unit_of_measurement": "kW"},
        "last_changed": now.isoformat(),
        "last_reported": now.isoformat(),
        "last_updated": now.isoformat(),
    }

    intervals = source.mapper(
        HomeAssistantHistoryPayload(history=history, current_state=current_state)
    )

    assert len(intervals) == 48
    assert intervals[0].start == datetime(2025, 1, 2, 6, 0, tzinfo=UTC)
    assert intervals[0].value == pytest.approx(1.0)  # type: ignore[reportUnknownMemberType]
    assert intervals[6].start == datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
    assert intervals[6].value == pytest.approx(2.0)  # type: ignore[reportUnknownMemberType]
    assert intervals[24].start == datetime(2025, 1, 3, 6, 0, tzinfo=UTC)
    assert intervals[24].value == pytest.approx(1.0)  # type: ignore[reportUnknownMemberType]
    assert intervals[30].start == datetime(2025, 1, 3, 12, 0, tzinfo=UTC)
    assert intervals[30].value == pytest.approx(2.0)  # type: ignore[reportUnknownMemberType]


def test_historical_average_smooths_realtime_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2025, 1, 2, 6, 30, tzinfo=UTC)
    _freeze_hass_source_time(monkeypatch, now)

    source = HomeAssistantHistoricalAverageForecastSource(
        type="home_assistant",
        platform="historical_average",
        entity="load_forecast",
        history_days=2,
        unit="kW",
        interval_duration=60,
        forecast_horizon_hours=3,
        realtime_window_minutes=120,
    )

    history: list[HomeAssistantHistoryStateDict] = [
        {
            "last_updated": datetime(2025, 1, 1, 7, 0, tzinfo=UTC).isoformat(),
            "state": 1.0,
        },
        {
            "last_updated": datetime(2025, 1, 1, 8, 0, tzinfo=UTC).isoformat(),
            "state": 1.0,
        },
        {
            "last_updated": datetime(2025, 1, 2, 5, 0, tzinfo=UTC).isoformat(),
            "state": 1.0,
        },
        {
            "last_updated": datetime(2025, 1, 2, 6, 0, tzinfo=UTC).isoformat(),
            "state": 1.0,
        },
    ]
    current_state: HomeAssistantStateDict = {
        "entity_id": "sensor.load",
        "state": 3.0,
        "attributes": {"unit_of_measurement": "kW"},
        "last_changed": now.isoformat(),
        "last_reported": now.isoformat(),
        "last_updated": now.isoformat(),
    }

    intervals = source.mapper(
        HomeAssistantHistoryPayload(history=history, current_state=current_state)
    )

    assert len(intervals) == 3
    assert intervals[0].start == datetime(2025, 1, 2, 6, 0, tzinfo=UTC)
    assert intervals[0].value == pytest.approx(3.0)  # type: ignore[reportUnknownMemberType]
    assert intervals[1].start == datetime(2025, 1, 2, 7, 0, tzinfo=UTC)
    assert intervals[1].value == pytest.approx(2.5)  # type: ignore[reportUnknownMemberType]
    assert intervals[2].start == datetime(2025, 1, 2, 8, 0, tzinfo=UTC)
    assert intervals[2].value == pytest.approx(1.5)  # type: ignore[reportUnknownMemberType]


def test_amber_forecast_falls_back_to_per_kwh_when_advanced_missing() -> None:
    source = HomeAssistantAmberElectricForecastSource(
        type="home_assistant",
        platform="amberelectric",
        entity="price_forecast",
        price_forecast_mode="advanced",
    )
    state: HomeAssistantStateDict = {
        "entity_id": "sensor.price_forecast",
        "state": "ok",
        "attributes": {
            "forecasts": [
                {
                    "start_time": "2026-01-07T03:30:01+00:00",
                    "end_time": "2026-01-07T03:35:00+00:00",
                    "advanced_price_predicted": None,
                    "per_kwh": 0.05,
                }
            ]
        },
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }

    intervals = source.mapper(state)

    assert len(intervals) == 1
    assert intervals[0].value == pytest.approx(0.05)  # type: ignore[reportUnknownMemberType]


@pytest.mark.parametrize(
    ("blend", "expected"),
    [
        ("blend_max", 0.12),
        ("blend_min", 0.08),
        ("blend_mean", 0.10),
    ],
)
def test_amber_forecast_blends_advanced_and_spot(
    blend: Literal["blend_max", "blend_min", "blend_mean"],
    expected: float,
) -> None:
    source = HomeAssistantAmberElectricForecastSource(
        type="home_assistant",
        platform="amberelectric",
        entity="price_forecast",
        price_forecast_mode=blend,
    )
    state: HomeAssistantStateDict = {
        "entity_id": "sensor.price_forecast",
        "state": "ok",
        "attributes": {
            "forecasts": [
                {
                    "start_time": "2026-01-07T03:30:01+00:00",
                    "end_time": "2026-01-07T03:35:00+00:00",
                    "advanced_price_predicted": 0.12,
                    "per_kwh": 0.08,
                }
            ]
        },
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }

    intervals = source.mapper(state)

    assert len(intervals) == 1
    assert intervals[0].value == pytest.approx(expected)  # type: ignore[reportUnknownMemberType]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("spot", 0.08),
        ("advanced", 0.12),
    ],
)
def test_amber_forecast_can_force_spot_or_advanced(
    mode: Literal["spot", "advanced"],
    expected: float,
) -> None:
    source = HomeAssistantAmberElectricForecastSource(
        type="home_assistant",
        platform="amberelectric",
        entity="price_forecast",
        price_forecast_mode=mode,
    )
    state: HomeAssistantStateDict = {
        "entity_id": "sensor.price_forecast",
        "state": "ok",
        "attributes": {
            "forecasts": [
                {
                    "start_time": "2026-01-07T03:30:01+00:00",
                    "end_time": "2026-01-07T03:35:00+00:00",
                    "advanced_price_predicted": 0.12,
                    "per_kwh": 0.08,
                }
            ]
        },
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }

    intervals = source.mapper(state)

    assert len(intervals) == 1
    assert intervals[0].value == pytest.approx(expected)  # type: ignore[reportUnknownMemberType]


def test_amber_forecast_spot_requires_spot_price() -> None:
    source = HomeAssistantAmberElectricForecastSource(
        type="home_assistant",
        platform="amberelectric",
        entity="price_forecast",
        price_forecast_mode="spot",
    )
    state: HomeAssistantStateDict = {
        "entity_id": "sensor.price_forecast",
        "state": "ok",
        "attributes": {
            "forecasts": [
                {
                    "start_time": "2026-01-07T03:30:01+00:00",
                    "end_time": "2026-01-07T03:35:00+00:00",
                    "advanced_price_predicted": 0.12,
                }
            ]
        },
        "last_changed": "2026-01-07T03:30:00+00:00",
        "last_reported": "2026-01-07T03:30:00+00:00",
        "last_updated": "2026-01-07T03:30:00+00:00",
    }

    with pytest.raises(ValueError, match="Spot price is required"):
        source.mapper(state)
