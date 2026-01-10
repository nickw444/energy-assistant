from __future__ import annotations

from datetime import UTC, datetime

import pytest

import hass_energy.lib.source_resolver.hass_source as hass_source
from hass_energy.lib.source_resolver.hass_provider import HomeAssistantHistoryPayload
from hass_energy.lib.source_resolver.hass_source import (
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

    history = [
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
    current_state = {
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
    assert intervals[0].value == pytest.approx(1.0)
    assert intervals[6].start == datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
    assert intervals[6].value == pytest.approx(2.0)
    assert intervals[24].start == datetime(2025, 1, 3, 6, 0, tzinfo=UTC)
    assert intervals[24].value == pytest.approx(1.0)
    assert intervals[30].start == datetime(2025, 1, 3, 12, 0, tzinfo=UTC)
    assert intervals[30].value == pytest.approx(2.0)


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

    history = [
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
    current_state = {
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
    assert intervals[0].value == pytest.approx(3.0)
    assert intervals[1].start == datetime(2025, 1, 2, 7, 0, tzinfo=UTC)
    assert intervals[1].value == pytest.approx(2.5)
    assert intervals[2].start == datetime(2025, 1, 2, 8, 0, tzinfo=UTC)
    assert intervals[2].value == pytest.approx(1.5)


def test_amber_forecast_falls_back_to_per_kwh_when_advanced_missing() -> None:
    source = HomeAssistantAmberElectricForecastSource(
        type="home_assistant",
        platform="amberelectric",
        entity="price_forecast",
        use_advanced_price_forecast=True,
    )
    state = {
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
    assert intervals[0].value == pytest.approx(0.05)
