import datetime
from dataclasses import dataclass


@dataclass
class PriceForecastInterval:
    start: datetime.datetime  # timezone-aware start time
    end: datetime.datetime  # timezone-aware end time
    value: float  # Value in local currency per kWh


@dataclass
class PowerForecastInterval:
    start: datetime.datetime  # timezone-aware start time
    end: datetime.datetime  # timezone-aware end time
    value: float  # Value in kW


@dataclass
class TemperatureForecastInterval:
    start: datetime.datetime  # timezone-aware start time
    end: datetime.datetime  # timezone-aware end time
    value: float  # Temperature value
