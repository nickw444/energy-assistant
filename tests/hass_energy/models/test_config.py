from __future__ import annotations

import pytest
from pydantic import ValidationError

from hass_energy.models.config import EmsConfig


def test_high_res_requires_both_fields() -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        EmsConfig(
            timestep_minutes=30,
            min_horizon_minutes=60,
            high_res_timestep_minutes=5,
        )


def test_high_res_horizon_requires_multiple_of_timestep() -> None:
    with pytest.raises(ValidationError, match="multiple"):
        EmsConfig(
            timestep_minutes=30,
            min_horizon_minutes=60,
            high_res_timestep_minutes=5,
            high_res_horizon_minutes=12,
        )


def test_battery_terminal_soc_shortfall_cost_per_kwh_requires_non_negative() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        EmsConfig(
            timestep_minutes=30,
            min_horizon_minutes=60,
            battery_terminal_soc_shortfall_cost_per_kwh=-0.01,
        )
