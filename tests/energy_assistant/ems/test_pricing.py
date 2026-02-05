from __future__ import annotations

from datetime import UTC, datetime

import pytest

from energy_assistant.ems.horizon import build_horizon
from energy_assistant.ems.pricing import PriceSeriesBuilder
from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantPowerKwEntitySource,
)
from energy_assistant.models.plant import GridConfig, GridPriceRiskConfig


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
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=2)
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
        price_import=[0.1, 0.1],
        price_export=[1.0, 1.0],
    )

    assert series.import_effective[1] == pytest.approx(0.45)
    assert series.export_effective[1] == pytest.approx(0.3)


def test_price_risk_floor_ceiling_applied_without_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=2)
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
        price_import=[0.1, 0.1],
        price_export=[0.8, 0.8],
    )

    assert series.import_effective[1] == pytest.approx(0.2)
    assert series.export_effective[1] == pytest.approx(0.5)


def test_price_risk_floor_ceiling_skipped_at_t0() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=60, num_intervals=2)
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
        price_import=[0.1, 0.1],
        price_export=[1.0, 1.0],
    )

    assert series.import_effective[0] == pytest.approx(0.15)
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


def test_export_effective_price_ceiling_full_risk_and_grid_bias() -> None:
    # Mirrors a "future window" export spike:
    # raw=19.95, export_price_ceiling=10.0, risk_bias=25% (full ramp), grid_bias=25%.
    #
    # Expected math (positive export prices are discounted):
    # clamp:  min(19.95, 10.0) = 10.0
    # risk:   10.0 * (1 - 0.25) = 7.5
    # grid:   7.5  * (1 - 0.25) = 5.625  (often displayed as 5.63 after rounding)
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _build_horizon(now=now, timestep_minutes=30, num_intervals=21)
    grid = _make_grid_config(
        grid_bias_pct=25.0,
        risk_bias_pct=25.0,
        ramp_start_after_minutes=30,
        ramp_duration_minutes=120,
        export_price_ceiling=10.0,
    )
    price_model = PriceSeriesBuilder(
        grid_price_bias_pct=grid.grid_price_bias_pct,
        grid_price_risk=grid.grid_price_risk,
    )
    raw_export = 19.95
    series = price_model.build_series(
        horizon=horizon,
        price_import=[0.0] * horizon.num_intervals,
        price_export=[raw_export] * horizon.num_intervals,
    )

    # Slot index 20 midpoint is 10h15m from `now`, well past the ramp end (150m),
    # so the full risk bias is in effect.
    assert series.export_effective[20] == pytest.approx(5.625)


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
