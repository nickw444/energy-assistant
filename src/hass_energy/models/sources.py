from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class HomeAssistantEntitySource(BaseModel):
    type: Literal["home_assistant"]
    entity: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HomeAssistantAmberElectricForecastSource(BaseModel):
    type: Literal["home_assistant"]
    platform: Literal["amberelectric"]
    entity: str = Field(min_length=1)
    use_advanced_price_forecast: bool | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HomeAssistantSolcastForecastSource(BaseModel):
    type: Literal["home_assistant"]
    platform: Literal["solcast"]
    entities: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


PriceForecastSource = HomeAssistantAmberElectricForecastSource
