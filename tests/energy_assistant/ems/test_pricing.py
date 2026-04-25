from __future__ import annotations

from datetime import UTC, datetime

import pytest

from energy_assistant.ems.planning.horizon import HorizonFactory
from energy_assistant.ems.planning.pricing import PriceSeriesBuilder
from energy_assistant.models.plant import (
    InputReference,
    PriceBiasFilterConfig,
    PriceBindingConfig,
    PriceRiskFilterConfig,
)


def _make_horizon(*, now: datetime, timestep_minutes: int, num_intervals: int):
    return HorizonFactory(
        timestep_minutes=timestep_minutes,
        horizon_minutes=timestep_minutes * num_intervals,
    ).build(now=now)


def _binding(*filters: PriceBiasFilterConfig | PriceRiskFilterConfig) -> PriceBindingConfig:
    return PriceBindingConfig(
        source=InputReference(source="inputs.price"),
        filters=list(filters),
    )


def test_price_risk_ramp_start_duration() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=30, num_intervals=5)
    builder = PriceSeriesBuilder()
    binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=100.0,
            ramp_start_after_minutes=30,
            ramp_duration_minutes=90,
        )
    )
    series = builder.build_series(
        horizon=horizon,
        price_import=[1.0] * horizon.num_intervals,
        import_binding=binding,
        price_export=[1.0] * horizon.num_intervals,
        export_binding=binding,
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
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    builder = PriceSeriesBuilder()
    import_binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
            import_price_floor=0.3,
        )
    )
    export_binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
            export_price_ceiling=0.6,
        )
    )
    series = builder.build_series(
        horizon=horizon,
        price_import=[0.1, 0.1],
        import_binding=import_binding,
        price_export=[1.0, 1.0],
        export_binding=export_binding,
    )

    assert series.import_effective[1] == pytest.approx(0.45)
    assert series.export_effective[1] == pytest.approx(0.3)


def test_price_risk_floor_ceiling_applied_without_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    builder = PriceSeriesBuilder()
    import_binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=0.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
            import_price_floor=0.2,
        )
    )
    export_binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=0.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
            export_price_ceiling=0.5,
        )
    )
    series = builder.build_series(
        horizon=horizon,
        price_import=[0.1, 0.1],
        import_binding=import_binding,
        price_export=[0.8, 0.8],
        export_binding=export_binding,
    )

    assert series.import_effective[1] == pytest.approx(0.2)
    assert series.export_effective[1] == pytest.approx(0.5)


def test_price_risk_floor_ceiling_skipped_at_t0() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    builder = PriceSeriesBuilder()
    import_binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
            import_price_floor=0.3,
        )
    )
    export_binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
            export_price_ceiling=0.6,
        )
    )
    series = builder.build_series(
        horizon=horizon,
        price_import=[0.1, 0.1],
        import_binding=import_binding,
        price_export=[1.0, 1.0],
        export_binding=export_binding,
    )

    assert series.import_effective[0] == pytest.approx(0.15)
    assert series.export_effective[0] == pytest.approx(0.5)


def test_sign_aware_bias_negative_prices() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=1)
    builder = PriceSeriesBuilder()
    binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
        )
    )
    series = builder.build_series(
        horizon=horizon,
        price_import=[-1.0],
        import_binding=binding,
        price_export=[-1.0],
        export_binding=binding,
    )

    assert series.import_effective[0] == pytest.approx(-0.5)
    assert series.export_effective[0] == pytest.approx(-1.5)


def test_combined_risk_and_grid_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=1)
    builder = PriceSeriesBuilder()
    binding = _binding(
        PriceBiasFilterConfig(type="bias", bias_pct=50.0),
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
        ),
    )
    series = builder.build_series(
        horizon=horizon,
        price_import=[1.0],
        import_binding=binding,
        price_export=[1.0],
        export_binding=binding,
    )

    assert series.import_effective[0] == pytest.approx(2.25)
    assert series.export_effective[0] == pytest.approx(0.25)


def test_export_effective_price_ceiling_full_risk_and_grid_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=30, num_intervals=21)
    builder = PriceSeriesBuilder()
    export_binding = _binding(
        PriceBiasFilterConfig(type="bias", bias_pct=25.0),
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=25.0,
            ramp_start_after_minutes=30,
            ramp_duration_minutes=120,
            export_price_ceiling=10.0,
        ),
    )
    raw_export = 19.95
    series = builder.build_series(
        horizon=horizon,
        price_import=[0.0] * horizon.num_intervals,
        import_binding=_binding(),
        price_export=[raw_export] * horizon.num_intervals,
        export_binding=export_binding,
    )

    assert series.export_effective[20] == pytest.approx(7.5)


def test_price_series_length_mismatch_raises() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    builder = PriceSeriesBuilder()
    binding = _binding()

    with pytest.raises(ValueError, match="price_import length"):
        builder.build_series(
            horizon=horizon,
            price_import=[1.0],
            import_binding=binding,
            price_export=[1.0, 1.0],
            export_binding=binding,
        )

    with pytest.raises(ValueError, match="price_export length"):
        builder.build_series(
            horizon=horizon,
            price_import=[1.0, 1.0],
            import_binding=binding,
            price_export=[1.0],
            export_binding=binding,
        )
