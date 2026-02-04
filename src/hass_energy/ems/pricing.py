from __future__ import annotations

from dataclasses import dataclass

from hass_energy.ems.horizon import Horizon
from hass_energy.models.plant import GridPriceRiskConfig


@dataclass(slots=True)
class PriceSeries:
    import_effective: list[float]
    export_effective: list[float]


class PriceSeriesBuilder:
    def __init__(
        self,
        *,
        grid_price_bias_pct: float,
        grid_price_risk: GridPriceRiskConfig | None,
    ) -> None:
        self._grid_bias_pct = float(grid_price_bias_pct)
        self._risk_cfg = grid_price_risk

    def build_series(
        self,
        *,
        horizon: Horizon,
        price_import: list[float],
        price_export: list[float],
    ) -> PriceSeries:
        if len(price_import) != horizon.num_intervals:
            raise ValueError("price_import length does not match horizon")
        if len(price_export) != horizon.num_intervals:
            raise ValueError("price_export length does not match horizon")

        import_effective: list[float] = []
        export_effective: list[float] = []

        for t, slot in enumerate(horizon.slots):
            raw_import = float(price_import[t])
            raw_export = float(price_export[t])
            midpoint = slot.start + (slot.end - slot.start) / 2
            minutes_from_now = max(0.0, (midpoint - horizon.now).total_seconds() / 60.0)
            risk_factor = self._risk_factor_minutes(minutes_from_now)
            risk_bias_pct = self._risk_cfg.bias_pct * risk_factor if self._risk_cfg else 0.0

            risk_import = self._apply_import_bias(raw_import, risk_bias_pct)
            risk_export = self._apply_export_bias(raw_export, risk_bias_pct)

            eff_import = self._apply_import_bias(risk_import, self._grid_bias_pct)
            eff_export = self._apply_export_bias(risk_export, self._grid_bias_pct)

            import_effective.append(eff_import)
            export_effective.append(eff_export)

        return PriceSeries(
            import_effective=import_effective,
            export_effective=export_effective,
        )

    def _risk_factor_minutes(self, minutes_from_now: float) -> float:
        if self._risk_cfg is None or self._risk_cfg.bias_pct <= 0:
            return 0.0
        cfg = self._risk_cfg
        start = float(cfg.ramp_start_after_minutes)
        duration = float(cfg.ramp_duration_minutes)
        if duration <= 0:
            return 1.0 if minutes_from_now >= start else 0.0
        if minutes_from_now <= start:
            return 0.0
        full_at = start + duration
        if minutes_from_now >= full_at:
            return 1.0
        return (minutes_from_now - start) / duration

    @staticmethod
    def _apply_import_bias(price: float, bias_pct: float) -> float:
        if bias_pct == 0:
            return price
        bias = bias_pct / 100.0
        if price >= 0:
            return price * (1.0 + bias)
        return price * (1.0 - bias)

    @staticmethod
    def _apply_export_bias(price: float, bias_pct: float) -> float:
        if bias_pct == 0:
            return price
        bias = bias_pct / 100.0
        if price >= 0:
            return price * (1.0 - bias)
        return price * (1.0 + bias)
