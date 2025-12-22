from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hass_energy.models.sources import HomeAssistantEntitySource


class SocIncentive(BaseModel):
    target_soc_pct: float = Field(ge=0, le=100)
    incentive: float

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ControlledEvLoad(BaseModel):
    name: str = Field(min_length=1)
    load_type: Literal["controlled_ev"]
    min_power_kw: float = Field(ge=0)
    max_power_kw: float = Field(ge=0)
    energy_kwh: float = Field(ge=0)
    connected: HomeAssistantEntitySource
    realtime_power: HomeAssistantEntitySource
    state_of_charge_pct: HomeAssistantEntitySource
    soc_incentives: list[SocIncentive] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_power_bounds(self) -> "ControlledEvLoad":
        if self.min_power_kw > self.max_power_kw:
            raise ValueError("min_power_kw must be <= max_power_kw")
        return self


class NonVariableLoad(BaseModel):
    name: str = Field(min_length=1)
    load_type: Literal["nonvariable_load"]

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


LoadConfig = Annotated[ControlledEvLoad | NonVariableLoad, Field(discriminator="load_type")]
