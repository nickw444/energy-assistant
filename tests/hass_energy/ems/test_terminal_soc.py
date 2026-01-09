from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.lib.home_assistant import HomeAssistantConfig
from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantHistoricalAverageForecastSource,
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
    HomeAssistantSolcastForecastSource,
)
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from hass_energy.models.config import AppConfig, EmsConfig, ServerConfig
from hass_energy.models.plant import (
    BatteryConfig,
    GridConfig,
    InverterConfig,
    PlantConfig,
    PlantLoadConfig,
    PvConfig,
    TimeWindow,
)


class DummyResolver:
    def __init__(
        self,
        *,
        price_forecasts: dict[str, list[PriceForecastInterval]],
        pv_forecasts: dict[str, list[PowerForecastInterval]],
        load_forecasts: dict[str, list[PowerForecastInterval]],
        realtime_values: dict[str, float],
    ) -> None:
        self._price_forecasts = price_forecasts
        self._pv_forecasts = pv_forecasts
        self._load_forecasts = load_forecasts
        self._realtime_values = realtime_values

    def resolve(self, source: object) -> object:
        if isinstance(source, HomeAssistantAmberElectricForecastSource):
            return self._price_forecasts[source.entity]
        if isinstance(source, HomeAssistantSolcastForecastSource):
            return self._pv_forecasts[source.entities[0]]
        if isinstance(source, HomeAssistantHistoricalAverageForecastSource):
            return self._load_forecasts[source.entity]
        if isinstance(
            source,
            (
                HomeAssistantPowerKwEntitySource,
                HomeAssistantCurrencyEntitySource,
                HomeAssistantPercentageEntitySource,
            ),
        ):
            return self._realtime_values[source.entity]
        raise TypeError(f"Unhandled source type: {type(source).__name__}")


def _power_intervals(
    start: datetime,
    *,
    interval_minutes: int,
    values: list[float],
) -> list[PowerForecastInterval]:
    intervals: list[PowerForecastInterval] = []
    cursor = start
    for value in values:
        slot_end = cursor + timedelta(minutes=interval_minutes)
        intervals.append(PowerForecastInterval(start=cursor, end=slot_end, value=value))
        cursor = slot_end
    return intervals


def _price_intervals(
    start: datetime,
    *,
    interval_minutes: int,
    values: list[float],
) -> list[PriceForecastInterval]:
    intervals: list[PriceForecastInterval] = []
    cursor = start
    for value in values:
        slot_end = cursor + timedelta(minutes=interval_minutes)
        intervals.append(PriceForecastInterval(start=cursor, end=slot_end, value=value))
        cursor = slot_end
    return intervals


def _make_config(*, terminal_shortfall_cost: float | None) -> AppConfig:
    timestep_minutes = 60
    grid = GridConfig(
        max_import_kw=10.0,
        max_export_kw=0.0,
        realtime_grid_power=HomeAssistantPowerKwEntitySource(type="home_assistant", entity="grid"),
        realtime_price_import=HomeAssistantCurrencyEntitySource(
            type="home_assistant", entity="price_import"
        ),
        realtime_price_export=HomeAssistantCurrencyEntitySource(
            type="home_assistant", entity="price_export"
        ),
        price_import_forecast=HomeAssistantAmberElectricForecastSource(
            type="home_assistant",
            platform="amberelectric",
            entity="price_import_forecast",
        ),
        price_export_forecast=HomeAssistantAmberElectricForecastSource(
            type="home_assistant",
            platform="amberelectric",
            entity="price_export_forecast",
        ),
        import_forbidden_periods=[TimeWindow(start="00:00", end="01:00")],
    )
    plant_load = PlantLoadConfig(
        realtime_load_power=HomeAssistantPowerKwEntitySource(type="home_assistant", entity="load"),
        forecast=HomeAssistantHistoricalAverageForecastSource(
            type="home_assistant",
            platform="historical_average",
            entity="load_forecast",
            history_days=1,
            unit="kW",
            interval_duration=timestep_minutes,
        ),
    )
    inverter = InverterConfig(
        id="inv",
        name="Inv",
        peak_power_kw=5.0,
        curtailment=None,
        pv=PvConfig(
            realtime_power=None,
            forecast=HomeAssistantSolcastForecastSource(
                type="home_assistant",
                platform="solcast",
                entities=["pv_forecast"],
            ),
        ),
        battery=BatteryConfig(
            capacity_kwh=10.0,
            storage_efficiency_pct=100.0,
            throughput_cost_per_kwh=0.0,
            min_soc_pct=0.0,
            max_soc_pct=100.0,
            reserve_soc_pct=0.0,
            max_charge_kw=5.0,
            max_discharge_kw=5.0,
            state_of_charge_pct=HomeAssistantPercentageEntitySource(
                type="home_assistant", entity="batt_soc"
            ),
            realtime_power=HomeAssistantPowerKwEntitySource(
                type="home_assistant", entity="batt_power"
            ),
        ),
    )
    plant = PlantConfig(grid=grid, load=plant_load, inverters=[inverter])
    ems = EmsConfig(
        timestep_minutes=timestep_minutes,
        min_horizon_minutes=timestep_minutes,
        battery_terminal_soc_shortfall_cost_per_kwh=terminal_shortfall_cost,
    )
    return AppConfig(
        server=ServerConfig(),
        homeassistant=HomeAssistantConfig(base_url="http://localhost", token="token"),
        ems=ems,
        plant=plant,
        loads=[],
    )


def test_terminal_soc_shortfall_penalty_avoids_end_of_horizon_recharge() -> None:
    now = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    start = now
    interval_minutes = 60
    prices_import = [0.5, 0.5]
    prices_export = [0.0, 0.0]
    load_kw = [5.0, 0.0]
    pv_kw = [0.0, 0.0]

    hard_config = _make_config(terminal_shortfall_cost=None)
    soft_config = _make_config(terminal_shortfall_cost=0.1)

    price_import_intervals = _price_intervals(
        start,
        interval_minutes=interval_minutes,
        values=prices_import,
    )
    price_export_intervals = _price_intervals(
        start,
        interval_minutes=interval_minutes,
        values=prices_export,
    )
    load_intervals = _power_intervals(
        start,
        interval_minutes=interval_minutes,
        values=load_kw,
    )
    pv_intervals = _power_intervals(
        start,
        interval_minutes=interval_minutes,
        values=pv_kw,
    )

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": price_import_intervals,
            "price_export_forecast": price_export_intervals,
        },
        pv_forecasts={"pv_forecast": pv_intervals},
        load_forecasts={"load_forecast": load_intervals},
        realtime_values={
            "load": load_kw[0],
            "price_import": prices_import[0],
            "price_export": prices_export[0],
            "batt_soc": 100.0,
            "batt_power": 0.0,
            "grid": 0.0,
        },
    )

    hard_plan = EmsMilpPlanner(hard_config, resolver=resolver).generate_ems_plan(now=now)
    soft_plan = EmsMilpPlanner(soft_config, resolver=resolver).generate_ems_plan(now=now)

    assert hard_plan.status == "Optimal"
    assert soft_plan.status == "Optimal"

    assert hard_plan.timesteps[0].grid.import_violation_kw == pytest.approx(0.0, abs=1e-3)
    assert soft_plan.timesteps[0].grid.import_violation_kw == pytest.approx(0.0, abs=1e-3)

    assert hard_plan.timesteps[1].grid.import_kw == pytest.approx(5.0, abs=1e-3)
    assert soft_plan.timesteps[1].grid.import_kw == pytest.approx(0.0, abs=1e-3)

    hard_inv = hard_plan.timesteps[1].inverters["inv"]
    soft_inv = soft_plan.timesteps[1].inverters["inv"]
    assert hard_inv.battery_charge_kw == pytest.approx(5.0, abs=1e-3)
    assert soft_inv.battery_charge_kw == pytest.approx(0.0, abs=1e-3)
