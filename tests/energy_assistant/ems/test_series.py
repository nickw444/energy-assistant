from __future__ import annotations

from datetime import UTC, datetime

import pytest

from energy_assistant.ems.horizon import Horizon, HorizonFactory
from energy_assistant.ems.series import bool_series, interval_series_points, state_series_points


def _horizon() -> Horizon:
    return HorizonFactory(timestep_minutes=30, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    )


def test_interval_series_points_validates_length() -> None:
    horizon = _horizon()
    with pytest.raises(ValueError, match="interval series length"):
        interval_series_points(horizon, [1.0])


def test_state_series_points_validates_length() -> None:
    horizon = _horizon()
    with pytest.raises(ValueError, match="state series length"):
        state_series_points(horizon, [1.0, 2.0])


def test_series_points_align_to_horizon_times() -> None:
    horizon = _horizon()
    interval_points = interval_series_points(horizon, [1.0, 2.0])
    state_points = state_series_points(horizon, [10.0, 11.0, 12.0])

    assert [point.time for point in interval_points] == [slot.start for slot in horizon.slots]
    assert [point.time for point in state_points] == [
        horizon.start,
        horizon.slots[0].end,
        horizon.slots[1].end,
    ]


def test_bool_series_coerces_numeric_values() -> None:
    assert bool_series([0.0, 1.0, -2.5, False, True]) == [False, True, True, False, True]
