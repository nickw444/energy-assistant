from __future__ import annotations

import pytest
from pydantic import ValidationError

from energy_assistant.models.config import EmsConfig
from energy_assistant.models.plant import BatteryComponentConfig


def test_high_res_requires_both_fields() -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        EmsConfig(
            timestep_minutes=30,
            horizon_minutes=60,
            high_res_timestep_minutes=5,
        )


def test_high_res_horizon_requires_multiple_of_timestep() -> None:
    with pytest.raises(ValidationError, match="multiple"):
        EmsConfig(
            timestep_minutes=30,
            horizon_minutes=60,
            high_res_timestep_minutes=5,
            high_res_horizon_minutes=12,
        )


def test_high_res_horizon_must_not_exceed_total_horizon() -> None:
    with pytest.raises(ValidationError, match="<= horizon_minutes"):
        EmsConfig(
            timestep_minutes=30,
            horizon_minutes=60,
            high_res_timestep_minutes=5,
            high_res_horizon_minutes=90,
        )


def test_battery_terminal_soc_defaults_to_adaptive() -> None:
    battery = BatteryComponentConfig(
        type="battery",
        connection="primary",
        name="Battery Primary",
        capacity_kwh=13.5,
        storage_efficiency_pct=95.0,
        min_soc_pct=10.0,
        max_soc_pct=100.0,
        reserve_soc_pct=20.0,
        state_of_charge_pct="battery_soc",
        realtime_power="battery_power",
    )

    assert battery.terminal_soc.mode == "adaptive"
    assert battery.terminal_soc.penalty_per_kwh == "median"
