import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantBinarySensorEntitySource,
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
)
from hass_energy.models.plant import TimeWindow


class SocIncentive(BaseModel):
    target_soc_pct: float = Field(ge=0, le=100)
    incentive: float

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _default_time_windows() -> list[TimeWindow]:
    return []


def _default_soc_incentives() -> list[SocIncentive]:
    return []


class ControlledEvLoad(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    load_type: Literal["controlled_ev"]
    min_power_kw: float = Field(ge=0)
    max_power_kw: float = Field(ge=0)
    energy_kwh: float = Field(ge=0)
    connected: HomeAssistantBinarySensorEntitySource
    # Combined availability signal (true when the EV can be connected).
    can_connect: HomeAssistantBinarySensorEntitySource | None = None
    # Time windows when connecting the EV is permitted (local time).
    allowed_connect_times: list[TimeWindow] = Field(default_factory=_default_time_windows)
    # Grace period from "now" before assuming the EV can be connected.
    connect_grace_minutes: int = Field(default=0, ge=0)
    realtime_power: HomeAssistantPowerKwEntitySource
    state_of_charge_pct: HomeAssistantPercentageEntitySource
    soc_incentives: list[SocIncentive] = Field(default_factory=_default_soc_incentives)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", value):
            raise ValueError("id must be lowercase letters, numbers, and underscores")
        return value

    @model_validator(mode="after")
    def _validate_power_bounds(self) -> Self:
        if self.min_power_kw > self.max_power_kw:
            raise ValueError("min_power_kw must be <= max_power_kw")
        return self


class NonVariableLoad(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    load_type: Literal["nonvariable_load"]

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", value):
            raise ValueError("id must be lowercase letters, numbers, and underscores")
        return value


LoadConfig = Annotated[ControlledEvLoad | NonVariableLoad, Field(discriminator="load_type")]
