from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from energy_assistant.ems.horizon import Horizon
from energy_assistant.models.plant import (
    PriceBiasFilterConfig,
    PriceBindingConfig,
    PriceFilterConfig,
    PriceRiskFilterConfig,
)


@dataclass(slots=True)
class PriceSeries:
    import_effective: list[float]
    export_effective: list[float]


class PriceSeriesBuilder:
    def build_series(
        self,
        *,
        horizon: Horizon,
        price_import: list[float],
        import_binding: PriceBindingConfig,
        price_export: list[float],
        export_binding: PriceBindingConfig,
    ) -> PriceSeries:
        if len(price_import) != horizon.num_intervals:
            raise ValueError("price_import length does not match horizon")
        if len(price_export) != horizon.num_intervals:
            raise ValueError("price_export length does not match horizon")
        return PriceSeries(
            import_effective=self.apply_binding(
                horizon=horizon,
                prices=price_import,
                binding=import_binding,
                direction="import",
            ),
            export_effective=self.apply_binding(
                horizon=horizon,
                prices=price_export,
                binding=export_binding,
                direction="export",
            ),
        )

    def apply_binding(
        self,
        *,
        horizon: Horizon,
        prices: list[float],
        binding: PriceBindingConfig,
        direction: Literal["import", "export"],
    ) -> list[float]:
        effective = [float(value) for value in prices]
        for filter_config in binding.filters:
            effective = self._apply_filter(
                horizon=horizon,
                prices=effective,
                filter_config=filter_config,
                direction=direction,
            )
        return effective

    def binding_bias_pct(
        self,
        *,
        binding: PriceBindingConfig,
        direction: Literal["import", "export"],
    ) -> float:
        effective = 0.0
        for filter_config in binding.filters:
            if isinstance(filter_config, PriceBiasFilterConfig):
                effective = self._apply_bias(
                    effective,
                    filter_config.bias_pct,
                    direction=direction,
                )
                continue
            effective = self._apply_bias(effective, filter_config.bias_pct, direction=direction)
        return effective

    def _apply_filter(
        self,
        *,
        horizon: Horizon,
        prices: list[float],
        filter_config: PriceFilterConfig,
        direction: Literal["import", "export"],
    ) -> list[float]:
        if isinstance(filter_config, PriceBiasFilterConfig):
            return [
                self._apply_bias(value, filter_config.bias_pct, direction=direction)
                for value in prices
            ]
        return self._apply_risk(
            horizon=horizon,
            prices=prices,
            filter_config=filter_config,
            direction=direction,
        )

    def _apply_risk(
        self,
        *,
        horizon: Horizon,
        prices: list[float],
        filter_config: PriceRiskFilterConfig,
        direction: Literal["import", "export"],
    ) -> list[float]:
        effective: list[float] = []
        for t, slot in enumerate(horizon.slots):
            price = float(prices[t])
            if t != 0:
                if direction == "import" and filter_config.import_price_floor is not None:
                    price = max(price, float(filter_config.import_price_floor))
                if direction == "export" and filter_config.export_price_ceiling is not None:
                    price = min(price, float(filter_config.export_price_ceiling))
            midpoint = slot.start + (slot.end - slot.start) / 2
            minutes_from_now = max(0.0, (midpoint - horizon.now).total_seconds() / 60.0)
            risk_factor = self._risk_factor_minutes(
                minutes_from_now=minutes_from_now,
                filter_config=filter_config,
            )
            bias_pct = float(filter_config.bias_pct) * risk_factor
            effective.append(self._apply_bias(price, bias_pct, direction=direction))
        return effective

    @staticmethod
    def _risk_factor_minutes(
        *,
        minutes_from_now: float,
        filter_config: PriceRiskFilterConfig,
    ) -> float:
        if filter_config.bias_pct <= 0:
            return 0.0
        start = float(filter_config.ramp_start_after_minutes)
        duration = float(filter_config.ramp_duration_minutes)
        if duration <= 0:
            return 1.0 if minutes_from_now >= start else 0.0
        if minutes_from_now <= start:
            return 0.0
        full_at = start + duration
        if minutes_from_now >= full_at:
            return 1.0
        return (minutes_from_now - start) / duration

    @staticmethod
    def _apply_bias(
        price: float,
        bias_pct: float,
        *,
        direction: Literal["import", "export"],
    ) -> float:
        if bias_pct == 0:
            return float(price)
        bias = bias_pct / 100.0
        if direction == "import":
            if price >= 0:
                return price * (1.0 + bias)
            return price * (1.0 - bias)
        if price >= 0:
            return price * (1.0 - bias)
        return price * (1.0 + bias)
