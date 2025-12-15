"""Example optimizer module for hass-energy."""

from __future__ import annotations

from typing import Any

from hass_energy.optimizer import HassEnergyOptimizer


class ExampleOptimizer(HassEnergyOptimizer):
    """
    Simple rule-based optimizer that consumes mapped output from `ExampleMapper` and
    selects a mode from SCRATCH.md.

    Inputs used:
    - power_w: inverter meter power (positive when exporting to grid)
    - price_per_kwh: current energy price
    - renewables_pct: grid renewables percentage
    """

    def __init__(self) -> None:
        self._required_entities: list[str] = []

    def required_entities(self) -> list[str]:
        return list(self._required_entities)

    def decide(self, mapped: dict[str, Any], entities: dict[str, Any]) -> dict[str, Any]:
        price = _to_float(mapped.get("price_per_kwh"))
        power = _to_float(mapped.get("power_w"))
        renewables = _to_int(mapped.get("renewables_pct"))

        mode = "SELF_CONSUME"
        reason = "Defaulting to self consume."

        if price is None and renewables is None:
            return _decision(reason, price, power, renewables, mode)

        if price is not None and price <= 0.12:
            mode = "GRID_CHARGE_BATTERY"
            reason = "Price is very low; prefer grid charging the battery."
            return _decision(reason, price, power, renewables, mode)

        if price is not None and price >= 0.45:
            if renewables is not None and renewables < 25 and (power is None or power <= 0):
                mode = "SELF_CONSUME_CURTAIL"
                reason = "Price is very high with low renewables; avoid export."
            else:
                mode = "EXPORT_MAX"
                reason = "Price is very high; maximise export."
            return _decision(reason, price, power, renewables, mode)

        if renewables is not None:
            if renewables >= 65 and power is not None and power > 0:
                mode = "EV_FROM_PV"
                reason = "High renewables and exporting; charge EV from PV surplus."
                return _decision(reason, price, power, renewables, mode)
            if renewables <= 20 and power is not None and power > 0:
                mode = "SELF_CONSUME_CURTAIL"
                reason = "Low renewables share while exporting; avoid sending to grid."
                return _decision(reason, price, power, renewables, mode)

        return _decision(reason, price, power, renewables, mode)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decision(
    reason: str,
    price: float | None,
    power: float | None,
    renewables: int | None,
    mode: str,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "inputs": {
            "power_w": power,
            "price_per_kwh": price,
            "renewables_pct": renewables,
        },
        "reason": reason,
        "knobs": {},
    }
