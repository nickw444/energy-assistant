from __future__ import annotations

import pytest
from pydantic import ValidationError

from energy_assistant.models.config import EmsConfig


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
