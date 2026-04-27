from __future__ import annotations

from datetime import UTC, datetime, timedelta

from energy_assistant.plotting.plan import major_tick_hour_interval_for_range


def test_major_tick_hour_interval_prefers_hour_boundaries() -> None:
    start = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=16, minutes=15)
    assert major_tick_hour_interval_for_range(start, end) == 2


def test_major_tick_hour_interval_scales_for_long_horizons() -> None:
    start = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=72)
    assert major_tick_hour_interval_for_range(start, end) == 8
