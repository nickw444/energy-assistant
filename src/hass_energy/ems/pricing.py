from __future__ import annotations

from collections.abc import Sequence


def apply_price_preferences(
    price_import: Sequence[float],
    price_export: Sequence[float],
    *,
    import_premium_per_kwh: float,
    export_premium_per_kwh: float,
) -> tuple[list[float], list[float]]:
    """Apply user price premiums to build effective import/export price series."""
    eff_import = [float(value) + float(import_premium_per_kwh) for value in price_import]
    eff_export = [float(value) - float(export_premium_per_kwh) for value in price_export]
    return eff_import, eff_export
