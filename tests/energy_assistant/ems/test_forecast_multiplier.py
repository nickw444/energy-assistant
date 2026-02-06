from __future__ import annotations

import math

import pytest

from energy_assistant.ems.forecast_multiplier import ForecastMultiplier


def test_forecast_multiplier_apply_empty_series() -> None:
    assert ForecastMultiplier(0.5).apply([]) == []


def test_forecast_multiplier_apply_multiplier_one_returns_copy() -> None:
    series = [1.0, 2.0]
    out = ForecastMultiplier(1.0).apply(series)

    assert out == [1.0, 2.0]
    assert out is not series


def test_forecast_multiplier_apply_scales_all_slots() -> None:
    series = [2.0, 4.0]
    out = ForecastMultiplier(0.5).apply(series)

    assert out == [1.0, 2.0]


def test_forecast_multiplier_apply_skip_first_slot() -> None:
    series = [2.0, 4.0]
    out = ForecastMultiplier(0.5).apply(series, skip_first_slot=True)

    assert out == [2.0, 2.0]


def test_forecast_multiplier_apply_skip_first_slot_singleton() -> None:
    out = ForecastMultiplier(0.5).apply([2.0], skip_first_slot=True)

    assert out == [2.0]


def test_forecast_multiplier_rejects_negative_multiplier() -> None:
    with pytest.raises(ValueError, match="multiplier must be >= 0"):
        ForecastMultiplier(-0.1)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_forecast_multiplier_rejects_nonfinite_multiplier(value: float) -> None:
    with pytest.raises(ValueError, match="multiplier must be finite"):
        ForecastMultiplier(value)
