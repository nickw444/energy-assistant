from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hass_energy.ems.solver import solve_once
from hass_energy.lib.home_assistant import HomeAssistantConfig
from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantHistoricalAverageForecastSource,
    HomeAssistantPowerKwEntitySource,
    HomeAssistantSolcastForecastSource,
)
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from hass_energy.models.config import AppConfig, EmsConfig, ServerConfig
from hass_energy.models.plant import (
    GridConfig,
    InverterConfig,
    PlantConfig,
    PlantLoadConfig,
    PvConfig,
)


class DummyResolver:
    def __init__(
        self,
        *,
        price_forecasts: dict[str, list[PriceForecastInterval]],
        pv_forecasts: dict[str, list[PowerForecastInterval]],
        realtime_values: dict[str, float],
        load_forecasts: dict[str, list[PowerForecastInterval]] | None = None,
    ) -> None:
        self._price_forecasts = price_forecasts
        self._pv_forecasts = pv_forecasts
        self._realtime_values = realtime_values
        self._load_forecasts = load_forecasts or {}

    def resolve(self, source: object) -> object:
        if isinstance(source, HomeAssistantAmberElectricForecastSource):
            return self._price_forecasts[source.entity]
        if isinstance(source, HomeAssistantSolcastForecastSource):
            return self._pv_forecasts[source.entities[0]]
        if isinstance(source, HomeAssistantHistoricalAverageForecastSource):
            return self._load_forecasts[source.entity]
        if isinstance(
            source,
            (HomeAssistantPowerKwEntitySource, HomeAssistantCurrencyEntitySource),
        ):
            return self._realtime_values[source.entity]
        raise TypeError(f"Unhandled source type: {type(source).__name__}")


def _make_config(
    *,
    inverters: list[InverterConfig] | None = None,
    load: PlantLoadConfig | None = None,
    interval_duration: int = 5,
    num_intervals: int = 2,
) -> AppConfig:
    grid = GridConfig(
        max_import_kw=10.0,
        max_export_kw=10.0,
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
        import_forbidden_periods=[],
    )
    default_load_forecast = HomeAssistantHistoricalAverageForecastSource(
        type="home_assistant",
        platform="historical_average",
        entity="load_forecast",
        history_days=1,
        unit="kW",
        interval_duration=interval_duration,
    )
    plant_load = load or PlantLoadConfig(
        realtime_load_power=HomeAssistantPowerKwEntitySource(
            type="home_assistant", entity="load"
        ),
        forecast=default_load_forecast,
    )
    if inverters is None:
        inverters = [
            InverterConfig(
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
                battery=None,
            )
        ]
    plant = PlantConfig(grid=grid, load=plant_load, inverters=inverters)
    ems = EmsConfig(interval_duration=interval_duration, num_intervals=num_intervals)
    return AppConfig(
        server=ServerConfig(),
        homeassistant=HomeAssistantConfig(base_url="http://localhost", token="token"),
        ems=ems,
        plant=plant,
        loads=[],
    )


def _load_intervals(
    now: datetime,
    config: AppConfig,
    value: float,
) -> list[PowerForecastInterval]:
    interval_minutes = config.ems.interval_duration
    start = now.replace(
        minute=(now.minute // interval_minutes) * interval_minutes,
        second=0,
        microsecond=0,
    )
    intervals: list[PowerForecastInterval] = []
    for idx in range(config.ems.num_intervals):
        slot_start = start + timedelta(minutes=idx * interval_minutes)
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        intervals.append(
            PowerForecastInterval(start=slot_start, end=slot_end, value=value)
        )
    return intervals


def test_solver_exports_with_positive_price() -> None:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    config = _make_config()
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.interval_duration)
    slot1_start = slot_end
    slot1_end = slot1_start + timedelta(minutes=config.ems.interval_duration)
    intervals_import = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.1),
        PriceForecastInterval(start=slot1_start, end=slot1_end, value=0.2),
    ]
    intervals_export = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.05),
        PriceForecastInterval(start=slot1_start, end=slot1_end, value=0.05),
    ]
    pv_intervals = [
        PowerForecastInterval(start=slot0, end=slot_end, value=2.0),
        PowerForecastInterval(start=slot1_start, end=slot1_end, value=2.0),
    ]

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": intervals_import,
            "price_export_forecast": intervals_export,
        },
        pv_forecasts={"pv_forecast": pv_intervals},
        load_forecasts={"load_forecast": _load_intervals(now, config, value=1.0)},
        realtime_values={
            "load": 1.0,
            "price_import": 0.3,
            "price_export": 0.05,
            "grid": 0.0,
        },
    )

    plan = solve_once(config, resolver=resolver, now=now)
    slots = plan["slots"]
    assert len(slots) == 2
    for slot in slots:
        assert abs(slot["grid_export_kw"] - 1.0) < 1e-6
        assert abs(slot["grid_import_kw"]) < 1e-6


def test_realtime_price_overrides_current_slot() -> None:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    config = _make_config()
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.interval_duration)
    slot1_start = slot_end
    slot1_end = slot1_start + timedelta(minutes=config.ems.interval_duration)

    intervals_import = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.1),
        PriceForecastInterval(start=slot1_start, end=slot1_end, value=0.2),
    ]
    intervals_export = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.05),
        PriceForecastInterval(start=slot1_start, end=slot1_end, value=0.05),
    ]
    pv_intervals = [
        PowerForecastInterval(start=slot0, end=slot_end, value=0.0),
        PowerForecastInterval(start=slot1_start, end=slot1_end, value=0.0),
    ]

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": intervals_import,
            "price_export_forecast": intervals_export,
        },
        pv_forecasts={"pv_forecast": pv_intervals},
        load_forecasts={"load_forecast": _load_intervals(now, config, value=0.0)},
        realtime_values={
            "load": 0.0,
            "price_import": 0.3,
            "price_export": 0.05,
            "grid": 0.0,
        },
    )

    plan = solve_once(config, resolver=resolver, now=now)
    assert plan["slots"][0]["price_import"] == 0.3
    assert plan["slots"][1]["price_import"] == 0.2


def test_load_forecast_aligns_to_horizon() -> None:
    now = datetime(2025, 12, 27, 0, 2, tzinfo=UTC)
    interval_duration = 5
    load_forecast = HomeAssistantHistoricalAverageForecastSource(
        type="home_assistant",
        platform="historical_average",
        entity="load_history",
        history_days=1,
        unit="kW",
        interval_duration=interval_duration,
    )
    load = PlantLoadConfig(
        realtime_load_power=HomeAssistantPowerKwEntitySource(
            type="home_assistant", entity="load"
        ),
        forecast=load_forecast,
    )
    config = _make_config(
        load=load,
        interval_duration=interval_duration,
        num_intervals=3,
    )
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot1 = slot0 + timedelta(minutes=interval_duration)
    slot2 = slot1 + timedelta(minutes=interval_duration)
    slot3 = slot2 + timedelta(minutes=interval_duration)
    load_intervals = [
        PowerForecastInterval(start=slot0, end=slot1, value=1.0),
        PowerForecastInterval(start=slot1, end=slot2, value=2.0),
        PowerForecastInterval(start=slot2, end=slot3, value=3.0),
    ]
    price_intervals = [
        PriceForecastInterval(start=slot0, end=slot1, value=0.0),
        PriceForecastInterval(start=slot1, end=slot2, value=0.0),
        PriceForecastInterval(
            start=slot2,
            end=slot2 + timedelta(minutes=interval_duration),
            value=0.0,
        ),
    ]
    pv_intervals = [
        PowerForecastInterval(start=slot0, end=slot1, value=0.0),
        PowerForecastInterval(start=slot1, end=slot2, value=0.0),
        PowerForecastInterval(
            start=slot2,
            end=slot2 + timedelta(minutes=interval_duration),
            value=0.0,
        ),
    ]

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": price_intervals,
            "price_export_forecast": price_intervals,
        },
        pv_forecasts={"pv_forecast": pv_intervals},
        load_forecasts={"load_history": load_intervals},
        realtime_values={
            "load": 9.0,
            "price_import": 0.0,
            "price_export": 0.0,
            "grid": 0.0,
        },
    )

    plan = solve_once(config, resolver=resolver, now=now)
    slots = plan["slots"]
    assert abs(slots[0]["load_kw"] - 9.0) < 1e-6
    assert abs(slots[1]["load_kw"] - 2.0) < 1e-6
    assert abs(slots[2]["load_kw"] - 3.0) < 1e-6


def test_pv_forecast_reused_per_inverter() -> None:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    inverters = [
        InverterConfig(
            name="A",
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
            battery=None,
        ),
        InverterConfig(
            name="B",
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
            battery=None,
        ),
    ]
    config = _make_config(inverters=inverters)
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.interval_duration)
    pv_intervals = [
        PowerForecastInterval(start=slot0, end=slot_end, value=1.5),
        PowerForecastInterval(
            start=slot_end,
            end=slot_end + timedelta(minutes=config.ems.interval_duration),
            value=1.5,
        ),
    ]
    price_import_intervals = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.1),
        PriceForecastInterval(
            start=slot_end,
            end=slot_end + timedelta(minutes=config.ems.interval_duration),
            value=0.1,
        ),
    ]
    price_export_intervals = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.0),
        PriceForecastInterval(
            start=slot_end,
            end=slot_end + timedelta(minutes=config.ems.interval_duration),
            value=0.0,
        ),
    ]
    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": price_import_intervals,
            "price_export_forecast": price_export_intervals,
        },
        pv_forecasts={"pv_forecast": pv_intervals},
        load_forecasts={"load_forecast": _load_intervals(now, config, value=0.0)},
        realtime_values={
            "load": 0.0,
            "price_import": 0.1,
            "price_export": 0.0,
            "grid": 0.0,
        },
    )

    plan = solve_once(config, resolver=resolver, now=now)
    slot = plan["slots"][0]
    assert abs(slot["pv_inverters"]["A"] - 1.5) < 1e-6
    assert abs(slot["pv_inverters"]["B"] - 1.5) < 1e-6
    assert abs(slot["pv_kw"] - 3.0) < 1e-6
    assert abs(slot["grid_export_kw"] - 3.0) < 1e-6


def test_load_aware_curtailment_blocks_export() -> None:
    now = datetime(2025, 12, 27, 9, 2, tzinfo=UTC)
    inverter = InverterConfig(
        name="Curtail",
        peak_power_kw=5.0,
        curtailment="load-aware",
        pv=PvConfig(
            realtime_power=None,
            forecast=HomeAssistantSolcastForecastSource(
                type="home_assistant",
                platform="solcast",
                entities=["pv_forecast"],
            ),
        ),
        battery=None,
    )
    config = _make_config(inverters=[inverter], num_intervals=1)
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.interval_duration)
    pv_intervals = [PowerForecastInterval(start=slot0, end=slot_end, value=2.0)]
    price_import = [PriceForecastInterval(start=slot0, end=slot_end, value=0.2)]
    price_export = [PriceForecastInterval(start=slot0, end=slot_end, value=-0.1)]
    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": price_import,
            "price_export_forecast": price_export,
        },
        pv_forecasts={"pv_forecast": pv_intervals},
        load_forecasts={"load_forecast": _load_intervals(now, config, value=0.5)},
        realtime_values={
            "load": 0.5,
            "price_import": 0.2,
            "price_export": -0.1,
            "grid": 0.0,
        },
    )

    plan = solve_once(config, resolver=resolver, now=now)
    slot = plan["slots"][0]
    assert slot["curtail_inverters"]["Curtail"] is True
    assert abs(slot["grid_export_kw"]) < 1e-6
    assert abs(slot["grid_import_kw"]) < 1e-6


def test_binary_curtailment_prefers_import_over_negative_export() -> None:
    now = datetime(2025, 12, 27, 9, 2, tzinfo=UTC)
    inverter = InverterConfig(
        name="Curtail",
        peak_power_kw=5.0,
        curtailment="binary",
        pv=PvConfig(
            realtime_power=None,
            forecast=HomeAssistantSolcastForecastSource(
                type="home_assistant",
                platform="solcast",
                entities=["pv_forecast"],
            ),
        ),
        battery=None,
    )
    config = _make_config(inverters=[inverter], num_intervals=1)
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.interval_duration)
    pv_intervals = [PowerForecastInterval(start=slot0, end=slot_end, value=2.0)]
    price_import = [PriceForecastInterval(start=slot0, end=slot_end, value=0.1)]
    price_export = [PriceForecastInterval(start=slot0, end=slot_end, value=-0.5)]
    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": price_import,
            "price_export_forecast": price_export,
        },
        pv_forecasts={"pv_forecast": pv_intervals},
        load_forecasts={"load_forecast": _load_intervals(now, config, value=0.5)},
        realtime_values={
            "load": 0.5,
            "price_import": 0.1,
            "price_export": -0.5,
            "grid": 0.0,
        },
    )

    plan = solve_once(config, resolver=resolver, now=now)
    slot = plan["slots"][0]
    assert slot["curtail_inverters"]["Curtail"] is True
    assert abs(slot["grid_export_kw"]) < 1e-6
    assert abs(slot["grid_import_kw"] - 0.5) < 1e-6
