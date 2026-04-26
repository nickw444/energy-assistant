from __future__ import annotations

from datetime import UTC, datetime

import pytest

from energy_assistant.ems.components.grid.price_bindings import PriceBindingApplicator
from energy_assistant.ems.horizon import HorizonFactory
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
    applicator = PriceBindingApplicator()
    binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=100.0,
            ramp_start_after_minutes=30,
            ramp_duration_minutes=90,
        )
    )
    effective = applicator.apply(
        horizon=horizon,
        prices=[1.0] * horizon.num_intervals,
        binding=binding,
        direction="import",
    )

    expected = [
        1.0,
        pytest.approx(1.1666667, rel=1e-6),
        pytest.approx(1.5, rel=1e-6),
        pytest.approx(1.8333333, rel=1e-6),
        2.0,
    ]
    assert effective == expected


def test_price_risk_floor_ceiling_applied_before_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    applicator = PriceBindingApplicator()
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
    import_effective = applicator.apply(
        horizon=horizon,
        prices=[0.1, 0.1],
        binding=import_binding,
        direction="import",
    )
    export_effective = applicator.apply(
        horizon=horizon,
        prices=[1.0, 1.0],
        binding=export_binding,
        direction="export",
    )

    assert import_effective[1] == pytest.approx(0.45)
    assert export_effective[1] == pytest.approx(0.3)


def test_price_risk_floor_ceiling_applied_without_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    applicator = PriceBindingApplicator()
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
    import_effective = applicator.apply(
        horizon=horizon,
        prices=[0.1, 0.1],
        binding=import_binding,
        direction="import",
    )
    export_effective = applicator.apply(
        horizon=horizon,
        prices=[0.8, 0.8],
        binding=export_binding,
        direction="export",
    )

    assert import_effective[1] == pytest.approx(0.2)
    assert export_effective[1] == pytest.approx(0.5)


def test_price_risk_floor_ceiling_skipped_at_t0() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    applicator = PriceBindingApplicator()
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
    import_effective = applicator.apply(
        horizon=horizon,
        prices=[0.1, 0.1],
        binding=import_binding,
        direction="import",
    )
    export_effective = applicator.apply(
        horizon=horizon,
        prices=[1.0, 1.0],
        binding=export_binding,
        direction="export",
    )

    assert import_effective[0] == pytest.approx(0.15)
    assert export_effective[0] == pytest.approx(0.5)


def test_sign_aware_bias_negative_prices() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=1)
    applicator = PriceBindingApplicator()
    binding = _binding(
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
        )
    )
    import_effective = applicator.apply(
        horizon=horizon,
        prices=[-1.0],
        binding=binding,
        direction="import",
    )
    export_effective = applicator.apply(
        horizon=horizon,
        prices=[-1.0],
        binding=binding,
        direction="export",
    )

    assert import_effective[0] == pytest.approx(-0.5)
    assert export_effective[0] == pytest.approx(-1.5)


def test_combined_risk_and_grid_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=1)
    applicator = PriceBindingApplicator()
    binding = _binding(
        PriceBiasFilterConfig(type="bias", bias_pct=50.0),
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
        ),
    )
    import_effective = applicator.apply(
        horizon=horizon,
        prices=[1.0],
        binding=binding,
        direction="import",
    )
    export_effective = applicator.apply(
        horizon=horizon,
        prices=[1.0],
        binding=binding,
        direction="export",
    )

    assert import_effective[0] == pytest.approx(2.25)
    assert export_effective[0] == pytest.approx(0.25)


def test_binding_bias_pct_compounds_configured_biases() -> None:
    applicator = PriceBindingApplicator()
    binding = _binding(
        PriceBiasFilterConfig(type="bias", bias_pct=50.0),
        PriceRiskFilterConfig(
            type="risk",
            bias_pct=50.0,
            ramp_start_after_minutes=0,
            ramp_duration_minutes=0,
        ),
    )

    assert applicator.binding_bias_pct(binding=binding, direction="import") == pytest.approx(125.0)
    assert applicator.binding_bias_pct(binding=binding, direction="export") == pytest.approx(75.0)


def test_export_effective_price_ceiling_full_risk_and_grid_bias() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=30, num_intervals=21)
    applicator = PriceBindingApplicator()
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
    effective = applicator.apply(
        horizon=horizon,
        prices=[raw_export] * horizon.num_intervals,
        binding=export_binding,
        direction="export",
    )

    assert effective[20] == pytest.approx(7.5)


def test_price_series_length_mismatch_raises() -> None:
    now = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    horizon = _make_horizon(now=now, timestep_minutes=60, num_intervals=2)
    applicator = PriceBindingApplicator()
    binding = _binding()

    with pytest.raises(ValueError, match="price series length"):
        applicator.apply(
            horizon=horizon,
            prices=[1.0],
            binding=binding,
            direction="import",
        )

    with pytest.raises(ValueError, match="price series length"):
        applicator.apply(
            horizon=horizon,
            prices=[1.0],
            binding=binding,
            direction="export",
        )
