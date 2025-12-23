from __future__ import annotations

import datetime
from typing import Annotated, Literal, TypeVar, cast

from pydantic import ConfigDict, Field

from hass_energy.lib.source_resolver.hass_provider import HomeAssistantStateDict
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from hass_energy.lib.source_resolver.sources import EntitySource

T = TypeVar("T")

def required_float(value: str|int|float|None) -> float:
    if value is None:
        raise ValueError("Value is required and cannot be None")
    return float(value)

def required_bool(value: str|int|float|None) -> bool:
    if value is None:
        raise ValueError("Value is required and cannot be None")
    return bool(value)

class HomeAssistantEntitySource(EntitySource[HomeAssistantStateDict, T]):
    type: Literal["home_assistant"]
    entity: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class HomeAssistantMultiEntitySource(EntitySource[list[HomeAssistantStateDict], T]):
    type: Literal["home_assistant"]
    entities: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class HomeAssistantPowerKwEntitySource(HomeAssistantEntitySource[float]):
    # Take raw state and normalize to power in kW
    def mapper(self, state: HomeAssistantStateDict) -> float:
        return required_float(state["state"])

class HomeAssistantBinarySensorEntitySource(HomeAssistantEntitySource[bool]):
    # Take raw state and normalize to boolean
    def mapper(self, state: HomeAssistantStateDict) -> bool:
        return required_bool(state['state'])

class HomeAssistantPercentageEntitySource(HomeAssistantEntitySource[float]):
    # Take raw state and normalize to percentage (0-100)
    def mapper(self, state: HomeAssistantStateDict) -> float:
        return required_float(state["state"])
    
class HomeAssistantCurrencyEntitySource(HomeAssistantEntitySource[float]):
    # Take raw state and normalize to currency value
    def mapper(self, state: HomeAssistantStateDict) -> float:
        return required_float(state["state"])

class HomeAssistantAmberElectricForecastSource(HomeAssistantEntitySource[PriceForecastInterval]):
    type: Literal["home_assistant"]
    platform: Literal["amberelectric"]
    entity: str = Field(min_length=1)
    use_advanced_price_forecast: bool | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def mapper(self, state: HomeAssistantStateDict) -> PriceForecastInterval:
        raise NotImplementedError()


class HomeAssistantSolcastForecastSource(HomeAssistantMultiEntitySource[list[PowerForecastInterval]]):
    platform: Literal["solcast"]
    
    type: Literal["home_assistant"]
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def mapper(self, state: list[HomeAssistantStateDict]) -> list[PowerForecastInterval]:
        now = datetime.datetime.now(tz=datetime.UTC)
        forecasts: list[PowerForecastInterval] = []
        for state_item in state:
            detailed_forecast = cast(list[dict[str, object]], state_item['attributes']['detailedForecast'])
            for solcast_interval in detailed_forecast:
                forecast_interval = self._solcast_to_forecast_interval(solcast_interval)
                if forecast_interval.end < now:
                    continue
                forecasts.append(forecast_interval)

        print(forecasts)
        return forecasts

    def _solcast_to_forecast_interval(self, solcast_interval: dict[str, object]) -> PowerForecastInterval:
        start_dt = datetime.datetime.fromisoformat(str(solcast_interval['period_start']))
        end_dt = start_dt + datetime.timedelta(minutes=30)
        return PowerForecastInterval(
            start=start_dt,
            end=end_dt,
            value=float(solcast_interval["pv_estimate"]),
        )
    
