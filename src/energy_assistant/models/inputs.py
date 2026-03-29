from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantAmberExpressForecastSource,
    HomeAssistantHistoricalAverageForecastSource,
    HomeAssistantSolcastForecastSource,
)


class InputValueKind(str, Enum):
    POWER = "power"
    PRICE = "price"
    PERCENTAGE = "percentage"
    BOOLEAN = "boolean"


class HomeAssistantScalarSource(BaseModel):
    type: Literal["home_assistant"]
    entity: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


ScalarSource = HomeAssistantScalarSource

PriceForecastSource = (
    HomeAssistantAmberElectricForecastSource | HomeAssistantAmberExpressForecastSource
)
PowerForecastSource = (
    HomeAssistantHistoricalAverageForecastSource | HomeAssistantSolcastForecastSource
)
ForecastSource = PriceForecastSource | PowerForecastSource


class ForecastExpansionConfig(BaseModel):
    history_days: int = Field(ge=1)
    interval_duration: int = Field(default=30, ge=1, le=60)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_interval_duration(self) -> Self:
        if 60 % self.interval_duration != 0:
            raise ValueError("interval_duration must evenly divide 60 minutes")
        return self


class ScalarInputConfig(BaseModel):
    type: Literal["scalar"]
    value_kind: InputValueKind
    source: ScalarSource

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ForecastInputConfig(BaseModel):
    type: Literal["forecast"]
    forecast: ForecastSource
    realtime: ScalarSource | None = None
    forecast_expansion: ForecastExpansionConfig | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _validate_input_shape(self) -> Self:
        forecast_kind = forecast_source_value_kind(self.forecast)
        if self.forecast_expansion is not None:
            if forecast_kind is not InputValueKind.PRICE:
                raise ValueError("forecast_expansion is only supported for price forecasts")
            if self.realtime is None:
                raise ValueError("forecast_expansion requires a realtime scalar source")
        return self


InputConfig = Annotated[ScalarInputConfig | ForecastInputConfig, Field(discriminator="type")]


def forecast_source_value_kind(source: ForecastSource) -> InputValueKind:
    if isinstance(
        source,
        (
            HomeAssistantAmberElectricForecastSource,
            HomeAssistantAmberExpressForecastSource,
        ),
    ):
        return InputValueKind.PRICE
    return InputValueKind.POWER


def input_value_kind(input_config: InputConfig) -> InputValueKind:
    if isinstance(input_config, ScalarInputConfig):
        return input_config.value_kind
    return forecast_source_value_kind(input_config.forecast)
