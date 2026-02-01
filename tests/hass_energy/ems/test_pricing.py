from __future__ import annotations

from hass_energy.ems.pricing import apply_price_preferences


def test_apply_price_preferences() -> None:
    eff_import, eff_export = apply_price_preferences(
        [20.0, 30.0],
        [8.0, 5.5],
        import_premium_per_kwh=5.0,
        export_premium_per_kwh=2.0,
    )
    assert eff_import == [25.0, 35.0]
    assert eff_export == [6.0, 3.5]
