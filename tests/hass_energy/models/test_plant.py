from __future__ import annotations

import pytest
from pydantic import ValidationError

from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
)
from hass_energy.models.plant import BatteryConfig, BatteryWearNone, BatteryWearSymmetric


def _battery_payload(*, wear: object) -> dict[str, object]:
    return {
        "capacity_kwh": 10.0,
        "storage_efficiency_pct": 95.0,
        "wear": wear,
        "min_soc_pct": 10.0,
        "max_soc_pct": 100.0,
        "reserve_soc_pct": 10.0,
        "max_charge_kw": 5.0,
        "max_discharge_kw": 5.0,
        "state_of_charge_pct": HomeAssistantPercentageEntitySource(
            type="home_assistant",
            entity="sensor.battery_soc",
        ),
        "realtime_power": HomeAssistantPowerKwEntitySource(
            type="home_assistant",
            entity="sensor.battery_power",
        ),
    }


def test_battery_wear_mode_none_validates() -> None:
    payload = _battery_payload(wear=BatteryWearNone(mode="none"))
    battery = BatteryConfig.model_validate(payload)
    assert battery.wear.mode == "none"


def test_battery_wear_symmetric_requires_non_negative_cost() -> None:
    payload = _battery_payload(wear={"mode": "symmetric", "cost_per_kwh": -0.1})
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        BatteryConfig.model_validate(payload)


def test_battery_wear_symmetric_accepts_cost() -> None:
    payload = _battery_payload(wear=BatteryWearSymmetric(mode="symmetric", cost_per_kwh=0.05))
    battery = BatteryConfig.model_validate(payload)
    assert battery.wear.mode == "symmetric"
