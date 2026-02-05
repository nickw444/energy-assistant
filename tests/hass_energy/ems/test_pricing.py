from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hass_energy.ems.horizon import build_horizon
from hass_energy.ems.pricing import PriceSeriesBuilder
from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantPowerKwEntitySource,
)
from hass_energy.models.plant import GridConfig, GridPriceRiskConfig


def _make_grid_config(
    *,
    grid_bias_pct: float = 0.0,
    risk_bias_pct: float = 0.0,
    ramp_start_after_minutes: int = 0,
    ramp_duration_minutes: int = 0,
    import_price_floor: float | None = None,
    export_price_ceiling: float | None = None,
) -> GridConfig:
    return GridConfig(
        max_import_kw=10.0,
        max_export_kw=10.0,
        realtime_grid_power=HomeAssistantPowerKwEntitySource(type="home_assistant", entity="grid"),
        realtime_price_import=HomeAssistantCurrencyEntitySource(
            type="home_assistant", entity="price_import"
        ),
        realtime_price_export=HomeAssistantCurrencyEntitySource(
            type="home_assistant", entity="price_export"
        ),
        price_import_forecast=HomeAssistantAmberElectricForecastSource(
            type="home_assistant", platform="amberelectric", entity="price_import_forecast"
        ),
        price_export_forecast=HomeAssistantAmberElectricForecastSource(
            type="home_assistant", platform="amberelectric", entity="price_export_forecast"
        ),
        grid_price_bias_pct=grid_bias_pct,
        grid_price_risk=GridPriceRiskConfig(
            bias_pct=risk_bias_pct,
            ramp_start_after_minutes=ramp_start_after_minutes,
            ramp_duration_minutes=ramp_duration_minutes,
            import_price_floor=import_price_floor,
            export_price_ceiling=export_price_ceiling,
        ),
        import_forbidden_periods=[],
    )


def _build_horizon(*, now: datetime, timestep_minutes: int, num_intervals: int):
    return build_horizon(
        now=now,
        timestep_minutes=timestep_minutes,
        num_intervals=num_intervals,
    )


def test_price_risk_ramp_start_duration() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=30, num_intervals=5)
    grid = _make_grid_config(
        risk_bias_pct=100.0,
        ramp_start_after_minutes=30,
        ramp_duration_minutes=90,
    )
    price_model = PriceSeriesBuilder(
        grid_price_bias_pct=grid.grid_price_bias_pct,
        grid_price_risk=grid.grid_price_risk,
    )
    series = price_model.build_series(
        horizon=horizon,
        price_import=[1.0] * horizon.num_intervals,
        price_export=[1.0] * horizon.num_intervals,
    )

    expected = [
        1.0,
        pytest.approx(1.1666667, rel=1e-6),
        pytest.approx(1.5, rel=1e-6),
        pytest.approx(1.8333333, rel=1e-6),
        2.0,
    ]
    assert series.import_effective == expected


def test_price_risk_floor_ceiling_applied_before_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=1)
    grid = _make_grid_config(
        risk_bias_pct=50.0,
        ramp_start_after_minutes=0,
        ramp_duration_minutes=0,
        import_price_floor=0.3,
        export_price_ceiling=0.6,
    )
    price_model = PriceSeriesBuilder(
        grid_price_bias_pct=grid.grid_price_bias_pct,
        grid_price_risk=grid.grid_price_risk,
    )
    series = price_model.build_series(
        horizon=horizon,
        price_import=[0.1],
        price_export=[1.0],
    )

    assert series.import_effective[0] == pytest.approx(0.45)
    assert series.export_effective[0] == pytest.approx(0.3)


def test_price_risk_floor_ceiling_applied_without_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=1)
    grid = _make_grid_config(
        risk_bias_pct=0.0,
        ramp_start_after_minutes=0,
        ramp_duration_minutes=0,
        import_price_floor=0.2,
        export_price_ceiling=0.5,
    )
    price_model = PriceSeriesBuilder(
        grid_price_bias_pct=grid.grid_price_bias_pct,
        grid_price_risk=grid.grid_price_risk,
    )
    series = price_model.build_series(
        horizon=horizon,
        price_import=[0.1],
        price_export=[0.8],
    )

    assert series.import_effective[0] == pytest.approx(0.2)
    assert series.export_effective[0] == pytest.approx(0.5)


def test_sign_aware_bias_negative_prices() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=1)
    grid = _make_grid_config(
        risk_bias_pct=50.0,
        ramp_start_after_minutes=0,
        ramp_duration_minutes=0,
    )
    price_model = PriceSeriesBuilder(
        grid_price_bias_pct=grid.grid_price_bias_pct,
        grid_price_risk=grid.grid_price_risk,
    )
    series = price_model.build_series(
        horizon=horizon,
        price_import=[-1.0],
        price_export=[-1.0],
    )

    assert series.import_effective[0] == pytest.approx(-0.5)
    assert series.export_effective[0] == pytest.approx(-1.5)


def test_combined_risk_and_grid_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=1)
    grid = _make_grid_config(
        grid_bias_pct=50.0,
        risk_bias_pct=50.0,
        ramp_start_after_minutes=0,
        ramp_duration_minutes=0,
    )
    price_model = PriceSeriesBuilder(
        grid_price_bias_pct=grid.grid_price_bias_pct,
        grid_price_risk=grid.grid_price_risk,
    )
    series = price_model.build_series(
        horizon=horizon,
        price_import=[1.0],
        price_export=[1.0],
    )

    assert series.import_effective[0] == pytest.approx(2.25)
    assert series.export_effective[0] == pytest.approx(0.25)


def test_price_series_length_mismatch_raises() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=2)
    grid = _make_grid_config()
    builder = PriceSeriesBuilder(
        grid_price_bias_pct=grid.grid_price_bias_pct,
        grid_price_risk=grid.grid_price_risk,
    )

    with pytest.raises(ValueError, match="price_import length"):
        builder.build_series(horizon=horizon, price_import=[1.0], price_export=[1.0, 1.0])

    with pytest.raises(ValueError, match="price_export length"):
        builder.build_series(horizon=horizon, price_import=[1.0, 1.0], price_export=[1.0])
