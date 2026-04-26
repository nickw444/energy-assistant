from __future__ import annotations

from typing import Literal

from energy_assistant.ems.horizon import Horizon
from energy_assistant.models.plant import (
    PriceBiasFilterConfig,
    PriceBindingConfig,
    PriceRiskFilterConfig,
)

PriceDirection = Literal["import", "export"]


class PriceBindingApplicator:
    def apply(
        self,
        *,
        horizon: Horizon,
        prices: list[float],
        binding: PriceBindingConfig,
        direction: PriceDirection,
    ) -> list[float]:
        if len(prices) != horizon.num_intervals:
            raise ValueError("price series length does not match horizon")

        effective = list(prices)
        for filter_config in binding.filters:
            if isinstance(filter_config, PriceBiasFilterConfig):
                effective = self._apply_bias_filter(
                    prices=effective,
                    bias_pct=filter_config.bias_pct,
                    direction=direction,
                )
            else:
                effective = self._apply_risk_filter(
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
        direction: PriceDirection,
    ) -> float:
        """Return the compounded full-strength bias from a binding's filters."""
        effective_multiplier = 1.0
        for filter_config in binding.filters:
            bias = filter_config.bias_pct / 100.0
            if direction == "import":
                effective_multiplier *= 1.0 + bias
            else:
                effective_multiplier *= 1.0 - bias
        if direction == "import":
            return (effective_multiplier - 1.0) * 100.0
        return (1.0 - effective_multiplier) * 100.0

    def _apply_bias_filter(
        self,
        *,
        prices: list[float],
        bias_pct: float,
        direction: PriceDirection,
    ) -> list[float]:
        return [self._apply_bias(price, bias_pct, direction=direction) for price in prices]

    def _apply_risk_filter(
        self,
        *,
        horizon: Horizon,
        prices: list[float],
        filter_config: PriceRiskFilterConfig,
        direction: PriceDirection,
    ) -> list[float]:
        effective: list[float] = []
        for t, slot in enumerate(horizon.slots):
            price = prices[t]
            if (
                t != 0
                and direction == "import"
                and filter_config.import_price_floor is not None
            ):
                price = max(price, filter_config.import_price_floor)
            if (
                t != 0
                and direction == "export"
                and filter_config.export_price_ceiling is not None
            ):
                price = min(price, filter_config.export_price_ceiling)
            midpoint = slot.start + (slot.end - slot.start) / 2
            minutes_from_now = max(0.0, (midpoint - horizon.now).total_seconds() / 60.0)
            risk_factor = self._risk_factor_minutes(
                minutes_from_now=minutes_from_now,
                filter_config=filter_config,
            )
            bias_pct = filter_config.bias_pct * risk_factor
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
        start = filter_config.ramp_start_after_minutes
        duration = filter_config.ramp_duration_minutes
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
        direction: PriceDirection,
    ) -> float:
        if bias_pct == 0:
            return price
        bias = bias_pct / 100.0
        if direction == "import":
            if price >= 0:
                return price * (1.0 + bias)
            return price * (1.0 - bias)
        if price >= 0:
            return price * (1.0 - bias)
        return price * (1.0 + bias)
