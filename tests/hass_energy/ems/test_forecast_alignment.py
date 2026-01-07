from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hass_energy.ems.forecast_alignment import (
    PowerForecastAligner,
    PriceForecastAligner,
    forecast_coverage_slots,
)
from hass_energy.ems.horizon import Horizon, HorizonSlot
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval


def _make_horizon(start: datetime, interval_minutes: int, num_intervals: int) -> Horizon:
    slots: list[HorizonSlot] = []
    for idx in range(num_intervals):
        slot_start = start + timedelta(minutes=idx * interval_minutes)
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        slots.append(HorizonSlot(index=idx, start=slot_start, end=slot_end))
    return Horizon(
        now=start,
        start=start,
        interval_minutes=interval_minutes,
        num_intervals=num_intervals,
        slots=slots,
    )


def test_power_aligner_exact_intervals() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=5, num_intervals=2)
    intervals = [
        PowerForecastInterval(start=start, end=start + timedelta(minutes=5), value=1.0),
        PowerForecastInterval(
            start=start + timedelta(minutes=5),
            end=start + timedelta(minutes=10),
            value=2.0,
        ),
    ]

    series = PowerForecastAligner().align(horizon, intervals)

    assert series == [1.0, 2.0]


def test_price_aligner_raises_when_horizon_exceeds_forecast() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=5, num_intervals=3)
    intervals = [
        PriceForecastInterval(start=start, end=start + timedelta(minutes=5), value=0.1),
        PriceForecastInterval(
            start=start + timedelta(minutes=5),
            end=start + timedelta(minutes=10),
            value=0.2,
        ),
    ]

    with pytest.raises(ValueError, match="does not cover the full horizon"):
        PriceForecastAligner().align(horizon, intervals)


def test_price_aligner_allows_current_slot_gap_with_override() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=5, num_intervals=2)
    intervals = [
        PriceForecastInterval(
            start=start + timedelta(minutes=5),
            end=start + timedelta(minutes=10),
            value=0.2,
        ),
    ]

    series = PriceForecastAligner().align(
        horizon,
        intervals,
        first_slot_override=0.35,
    )

    assert series == [0.35, 0.2]


def test_forecast_coverage_allows_missing_first_slot() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    interval_minutes = 5
    intervals = [
        PowerForecastInterval(
            start=start + timedelta(minutes=5),
            end=start + timedelta(minutes=10),
            value=1.0,
        ),
        PowerForecastInterval(
            start=start + timedelta(minutes=10),
            end=start + timedelta(minutes=15),
            value=1.0,
        ),
    ]

    coverage = forecast_coverage_slots(
        start,
        interval_minutes,
        intervals,
        allow_first_slot_missing=True,
    )

    assert coverage == 3


def test_forecast_coverage_stops_after_gap() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    interval_minutes = 5
    intervals = [
        PowerForecastInterval(
            start=start + timedelta(minutes=10),
            end=start + timedelta(minutes=15),
            value=1.0,
        ),
    ]

    coverage = forecast_coverage_slots(
        start,
        interval_minutes,
        intervals,
        allow_first_slot_missing=True,
    )

    assert coverage == 1
