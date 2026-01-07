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


def test_price_aligner_raises_missing_first_slot_without_override() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=5, num_intervals=2)
    intervals = [
        PriceForecastInterval(
            start=start + timedelta(minutes=5),
            end=start + timedelta(minutes=10),
            value=0.2,
        ),
    ]

    with pytest.raises(ValueError, match="does not cover the full horizon"):
        PriceForecastAligner().align(horizon, intervals)


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


def test_forecast_coverage_missing_first_slot_without_override() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    interval_minutes = 5
    intervals = [
        PowerForecastInterval(
            start=start + timedelta(minutes=5),
            end=start + timedelta(minutes=10),
            value=1.0,
        ),
    ]

    coverage = forecast_coverage_slots(
        start,
        interval_minutes,
        intervals,
        allow_first_slot_missing=False,
    )

    assert coverage == 0


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


def test_power_aligner_averages_over_longer_slot() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=30, num_intervals=1)
    intervals = [
        PowerForecastInterval(
            start=start,
            end=start + timedelta(minutes=10),
            value=1.0,
        ),
        PowerForecastInterval(
            start=start + timedelta(minutes=10),
            end=start + timedelta(minutes=20),
            value=2.0,
        ),
        PowerForecastInterval(
            start=start + timedelta(minutes=20),
            end=start + timedelta(minutes=30),
            value=3.0,
        ),
    ]

    series = PowerForecastAligner().align(horizon, intervals)

    assert series == [2.0]


def test_price_aligner_averages_over_longer_slot() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=15, num_intervals=1)
    intervals = [
        PriceForecastInterval(
            start=start,
            end=start + timedelta(minutes=10),
            value=0.1,
        ),
        PriceForecastInterval(
            start=start + timedelta(minutes=10),
            end=start + timedelta(minutes=15),
            value=0.3,
        ),
    ]

    series = PriceForecastAligner().align(horizon, intervals)

    assert series[0] == pytest.approx((0.1 * 10 + 0.3 * 5) / 15)


def test_power_aligner_handles_variable_slot_sizes() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    slots = [
        HorizonSlot(index=0, start=start, end=start + timedelta(minutes=5)),
        HorizonSlot(index=1, start=start + timedelta(minutes=5), end=start + timedelta(minutes=35)),
    ]
    horizon = Horizon(
        now=start,
        start=start,
        interval_minutes=5,
        num_intervals=len(slots),
        slots=slots,
    )
    intervals = [
        PowerForecastInterval(
            start=start + timedelta(minutes=5 * idx),
            end=start + timedelta(minutes=5 * (idx + 1)),
            value=float(idx + 1),
        )
        for idx in range(7)
    ]

    series = PowerForecastAligner().align(horizon, intervals)

    assert series[0] == 1.0
    assert series[1] == pytest.approx(4.5)


def test_power_aligner_raises_on_gap_inside_slot() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=30, num_intervals=1)
    intervals = [
        PowerForecastInterval(
            start=start,
            end=start + timedelta(minutes=10),
            value=1.0,
        ),
        PowerForecastInterval(
            start=start + timedelta(minutes=20),
            end=start + timedelta(minutes=30),
            value=1.0,
        ),
    ]

    with pytest.raises(ValueError, match="does not cover the full horizon"):
        PowerForecastAligner().align(horizon, intervals)


def test_power_aligner_allows_subsecond_gap() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=5, num_intervals=1)
    intervals = [
        PowerForecastInterval(
            start=start,
            end=start + timedelta(seconds=150),
            value=1.0,
        ),
        PowerForecastInterval(
            start=start + timedelta(seconds=150.5),
            end=start + timedelta(minutes=5),
            value=1.0,
        ),
    ]

    series = PowerForecastAligner().align(horizon, intervals)

    assert series == [1.0]


def test_power_aligner_rejects_zero_duration_series() -> None:
    start = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(start, interval_minutes=5, num_intervals=1)
    intervals = [
        PowerForecastInterval(start=start, end=start, value=1.0),
    ]

    with pytest.raises(ValueError, match="zero duration"):
        PowerForecastAligner().align(horizon, intervals)
