"""Example mapper module for hass-energy."""

from __future__ import annotations

from typing import Any

from hass_energy.mapper import (
    HassEnergyMapper,
    get_attr,
    get_state,
    to_float,
    to_int,
)


class ExampleMapper(HassEnergyMapper):
    """
    Example mapper that flattens two entities:
    - sensor.inverter_meter_power
    - sensor.amber_general_price
    """

    def __init__(self) -> None:
        self._entities = [
            "sensor.inverter_meter_power",
            "sensor.amber_general_price",
        ]

    def required_entities(self) -> list[str]:
        return list(self._entities)

    def map(self, states: dict[str, Any]) -> dict[str, Any]:
        power = get_state(states, "sensor.inverter_meter_power")
        price = get_state(states, "sensor.amber_general_price")
        renewables = get_attr(states, "sensor.amber_general_price", "renewables")

        return {
            "power_w": to_float(power),
            "price_per_kwh": to_float(price),
            "renewables_pct": to_int(renewables),
        }
