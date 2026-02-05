from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import pulp
import pytest

from hass_energy.ems.builder import MILPBuilder
from hass_energy.ems.horizon import build_horizon
from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.ems.pricing import PriceSeriesBuilder
from hass_energy.ems.time_windows import TimeWindowMatcher
from hass_energy.lib.home_assistant import HomeAssistantConfig
from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantAmberElectricForecastSource,
    HomeAssistantBinarySensorEntitySource,
    HomeAssistantCurrencyEntitySource,
    HomeAssistantHistoricalAverageForecastSource,
    HomeAssistantPercentageEntitySource,
    HomeAssistantPowerKwEntitySource,
    HomeAssistantSolcastForecastSource,
)
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.lib.source_resolver.sources import EntitySource
from hass_energy.models.config import AppConfig, EmsConfig, ServerConfig
from hass_energy.models.loads import ControlledEvLoad, LoadConfig
from hass_energy.models.plant import (
    GridConfig,
    InverterConfig,
    PlantConfig,
    PlantLoadConfig,
    PvConfig,
    TimeWindow,
)

Q = TypeVar("Q")
R = TypeVar("R")


class DummyResolver(ValueResolver):
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

    def mark_for_hydration(self, value: object) -> None:
        _ = value

    def hydrate_all(self) -> None:
        return

    def hydrate_history(self) -> None:
        return

    def hydrate_states(self) -> None:
        return

    def mark(self, source: object) -> None:
        _ = source

    def resolve(self, source: EntitySource[Q, R]) -> R:
        if isinstance(source, HomeAssistantAmberElectricForecastSource):
            return cast(R, self._price_forecasts[source.entity])
        if isinstance(source, HomeAssistantSolcastForecastSource):
            return cast(R, self._pv_forecasts[source.entities[0]])
        if isinstance(source, HomeAssistantHistoricalAverageForecastSource):
            return cast(R, self._load_forecasts[source.entity])
        if isinstance(
            source,
            (
                HomeAssistantPowerKwEntitySource,
                HomeAssistantCurrencyEntitySource,
                HomeAssistantBinarySensorEntitySource,
                HomeAssistantPercentageEntitySource,
            ),
        ):
            return cast(R, self._realtime_values[source.entity])
        raise TypeError(f"Unhandled source type: {type(source).__name__}")


def _make_config(
    *,
    inverters: list[InverterConfig] | None = None,
    load: PlantLoadConfig | None = None,
    timestep_minutes: int = 5,
    min_horizon_minutes: int | None = None,
    high_res_timestep_minutes: int | None = None,
    high_res_horizon_minutes: int | None = None,
    loads: list[LoadConfig] | None = None,
) -> AppConfig:
    if min_horizon_minutes is None:
        min_horizon_minutes = timestep_minutes * 2
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
        interval_duration=timestep_minutes,
    )
    plant_load = load or PlantLoadConfig(
        realtime_load_power=HomeAssistantPowerKwEntitySource(type="home_assistant", entity="load"),
        forecast=default_load_forecast,
    )
    if inverters is None:
        inverters = [
            InverterConfig(
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
                battery=None,
            )
        ]
    plant = PlantConfig(grid=grid, load=plant_load, inverters=inverters)
    ems = EmsConfig(
        timestep_minutes=timestep_minutes,
        min_horizon_minutes=min_horizon_minutes,
        high_res_timestep_minutes=high_res_timestep_minutes,
        high_res_horizon_minutes=high_res_horizon_minutes,
    )
    return AppConfig(
        server=ServerConfig(),
        homeassistant=HomeAssistantConfig(base_url="http://localhost", token="token"),
        ems=ems,
        plant=plant,
        loads=loads or [],
    )


def _load_intervals(
    now: datetime,
    config: AppConfig,
    value: float,
) -> list[PowerForecastInterval]:
    interval_minutes = config.ems.high_res_timestep_minutes or config.ems.timestep_minutes
    min_intervals = math.ceil(config.ems.min_horizon_minutes / interval_minutes)
    start = now.replace(
        minute=(now.minute // interval_minutes) * interval_minutes,
        second=0,
        microsecond=0,
    )
    intervals: list[PowerForecastInterval] = []
    for idx in range(min_intervals):
        slot_start = start + timedelta(minutes=idx * interval_minutes)
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        intervals.append(PowerForecastInterval(start=slot_start, end=slot_end, value=value))
    return intervals


def _power_intervals(
    start: datetime,
    *,
    interval_minutes: int,
    num_intervals: int,
    value: float,
) -> list[PowerForecastInterval]:
    intervals: list[PowerForecastInterval] = []
    for idx in range(num_intervals):
        slot_start = start + timedelta(minutes=idx * interval_minutes)
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        intervals.append(PowerForecastInterval(start=slot_start, end=slot_end, value=value))
    return intervals


def _require_value(value: float | None) -> float:
    assert value is not None
    return float(value)


def _price_intervals(
    start: datetime,
    *,
    interval_minutes: int,
    num_intervals: int,
    value: float,
) -> list[PriceForecastInterval]:
    intervals: list[PriceForecastInterval] = []
    for idx in range(num_intervals):
        slot_start = start + timedelta(minutes=idx * interval_minutes)
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        intervals.append(PriceForecastInterval(start=slot_start, end=slot_end, value=value))
    return intervals


def _solve_ev_switch_t0(
    *,
    realtime_power_kw: float,
    price_import: float,
) -> tuple[float, float]:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    ev_load = ControlledEvLoad(
        id="ev",
        name="EV",
        load_type="controlled_ev",
        min_power_kw=0.0,
        max_power_kw=0.1,
        energy_kwh=10.0,
        connected=HomeAssistantBinarySensorEntitySource(
            type="home_assistant",
            entity="ev_connected",
        ),
        can_connect=None,
        allowed_connect_times=[],
        connect_grace_minutes=0,
        realtime_power=HomeAssistantPowerKwEntitySource(
            type="home_assistant",
            entity="ev_power",
        ),
        state_of_charge_pct=HomeAssistantPercentageEntitySource(
            type="home_assistant",
            entity="ev_soc",
        ),
        soc_incentives=[],
        switch_penalty=0.02,
    )
    config = _make_config(
        timestep_minutes=60,
        min_horizon_minutes=60,
        loads=[ev_load],
    )
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)
    slot_start = horizon.start
    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": _price_intervals(
                slot_start,
                interval_minutes=60,
                num_intervals=1,
                value=price_import,
            ),
            "price_export_forecast": _price_intervals(
                slot_start,
                interval_minutes=60,
                num_intervals=1,
                value=0.0,
            ),
        },
        pv_forecasts={
            "pv_forecast": _power_intervals(
                slot_start,
                interval_minutes=60,
                num_intervals=1,
                value=0.0,
            ),
        },
        load_forecasts={
            "load_forecast": _power_intervals(
                slot_start,
                interval_minutes=60,
                num_intervals=1,
                value=0.0,
            ),
        },
        realtime_values={
            "load": 0.0,
            "price_import": price_import,
            "price_export": 0.0,
            "grid": 0.0,
            "ev_power": realtime_power_kw,
            "ev_soc": 50.0,
            "ev_connected": True,
        },
    )
    builder = MILPBuilder(
        config.plant,
        config.loads,
        resolver,
        config.ems,
        time_window_matcher=TimeWindowMatcher(),
        price_series_builder=PriceSeriesBuilder(
            grid_price_bias_pct=config.plant.grid.grid_price_bias_pct,
            grid_price_risk=config.plant.grid.grid_price_risk,
        ),
    )
    forecasts = builder.resolve_forecasts(now=now, interval_minutes=horizon.interval_minutes)
    model = builder.build(horizon=horizon, forecasts=forecasts)
    model.problem.solve(pulp.PULP_CBC_CMD(msg=False))
    ev_vars = model.loads.evs["ev"]
    return (
        _require_value(pulp.value(ev_vars.P_ev_charge_kw[0])),
        _require_value(pulp.value(ev_vars.Ev_charge_switch[0])),
    )


def test_ev_switch_t0_seed_uses_realtime_state() -> None:
    charge_on, switch_on = _solve_ev_switch_t0(realtime_power_kw=1.0, price_import=-1.0)
    charge_off, switch_off = _solve_ev_switch_t0(realtime_power_kw=0.0, price_import=-1.0)

    assert charge_on > 0.0
    assert charge_off > 0.0
    assert abs(switch_on) < 1e-6
    assert abs(switch_off - 1.0) < 1e-6


def test_ev_switch_t0_seed_when_turning_off() -> None:
    charge_on, switch_on = _solve_ev_switch_t0(realtime_power_kw=1.0, price_import=1.0)
    charge_off, switch_off = _solve_ev_switch_t0(realtime_power_kw=0.0, price_import=1.0)

    assert abs(charge_on) < 1e-6
    assert abs(charge_off) < 1e-6
    assert abs(switch_on - 1.0) < 1e-6
    assert abs(switch_off) < 1e-6

def test_builder_import_forbidden_periods_apply_via_model() -> None:
    config = _make_config(timestep_minutes=60, min_horizon_minutes=60)
    config.plant.grid.import_forbidden_periods = [
        TimeWindow(start="00:00", end="23:59", months=["jan"])
    ]

    def _import_allowed_for(now: datetime) -> list[bool]:
        horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)
        slot_start = horizon.start
        resolver = DummyResolver(
            price_forecasts={
                "price_import_forecast": _price_intervals(
                    slot_start,
                    interval_minutes=60,
                    num_intervals=1,
                    value=0.0,
                ),
                "price_export_forecast": _price_intervals(
                    slot_start,
                    interval_minutes=60,
                    num_intervals=1,
                    value=0.0,
                ),
            },
            pv_forecasts={
                "pv_forecast": _power_intervals(
                    slot_start,
                    interval_minutes=60,
                    num_intervals=1,
                    value=0.0,
                )
            },
            load_forecasts={
                "load_forecast": _power_intervals(
                    slot_start,
                    interval_minutes=60,
                    num_intervals=1,
                    value=0.0,
                )
            },
            realtime_values={
                "load": 0.0,
                "price_import": 0.0,
                "price_export": 0.0,
                "grid": 0.0,
            },
        )
        builder = MILPBuilder(
            config.plant,
            config.loads,
            resolver,
            config.ems,
            time_window_matcher=TimeWindowMatcher(),
            price_series_builder=PriceSeriesBuilder(
                grid_price_bias_pct=config.plant.grid.grid_price_bias_pct,
                grid_price_risk=config.plant.grid.grid_price_risk,
            ),
        )
        forecasts = builder.resolve_forecasts(now=now, interval_minutes=horizon.interval_minutes)
        model = builder.build(horizon=horizon, forecasts=forecasts)
        return model.grid.import_allowed

    assert _import_allowed_for(datetime(2025, 1, 15, 8, 0, tzinfo=UTC)) == [False]
    assert _import_allowed_for(datetime(2025, 3, 15, 8, 0, tzinfo=UTC)) == [True]


def test_zero_price_export_bonus_toggle_affects_objective() -> None:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    horizon = build_horizon(now=now, timestep_minutes=60, num_intervals=1)
    slot_start = horizon.start
    price_import = _price_intervals(
        slot_start,
        interval_minutes=60,
        num_intervals=1,
        value=0.1,
    )
    price_export = _price_intervals(
        slot_start,
        interval_minutes=60,
        num_intervals=1,
        value=0.0,
    )
    pv_intervals = _power_intervals(
        slot_start,
        interval_minutes=60,
        num_intervals=1,
        value=0.0,
    )
    load_intervals = _power_intervals(
        slot_start,
        interval_minutes=60,
        num_intervals=1,
        value=0.0,
    )

    def _objective_coeff(
        objective: pulp.LpAffineExpression | None, var: pulp.LpVariable
    ) -> float:
        assert objective is not None
        value = objective.get(var, 0.0)
        return 0.0 if value is None else float(value)

    def _build_coeff(prefer_export: bool) -> float:
        config = _make_config(timestep_minutes=60, min_horizon_minutes=60)
        config.plant.grid.zero_price_export_preference = (
            "export" if prefer_export else "curtail"
        )
        resolver = DummyResolver(
            price_forecasts={
                "price_import_forecast": price_import,
                "price_export_forecast": price_export,
            },
            pv_forecasts={"pv_forecast": pv_intervals},
            load_forecasts={"load_forecast": load_intervals},
            realtime_values={
                "load": 0.0,
                "price_import": 0.1,
                "price_export": 0.0,
                "grid": 0.0,
            },
        )
        builder = MILPBuilder(
            config.plant,
            config.loads,
            resolver,
            config.ems,
            time_window_matcher=TimeWindowMatcher(),
            price_series_builder=PriceSeriesBuilder(
                grid_price_bias_pct=config.plant.grid.grid_price_bias_pct,
                grid_price_risk=config.plant.grid.grid_price_risk,
            ),
        )
        forecasts = builder.resolve_forecasts(now=now, interval_minutes=horizon.interval_minutes)
        model = builder.build(horizon=horizon, forecasts=forecasts)
        return _objective_coeff(model.problem.objective, model.grid.P_export[0])

    coeff_prefer = _build_coeff(True)
    coeff_discourage = _build_coeff(False)
    dt_hours = horizon.dt_hours(0)
    assert coeff_prefer - coeff_discourage == pytest.approx(-2e-4 * dt_hours)


def test_solver_exports_with_positive_price() -> None:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    config = _make_config()
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.timestep_minutes)
    slot1_start = slot_end
    slot1_end = slot1_start + timedelta(minutes=config.ems.timestep_minutes)
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

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    timesteps = plan.timesteps
    assert len(timesteps) == 2
    for step in timesteps:
        assert abs(step.grid.export_kw - 1.0) < 1e-6
        assert abs(step.grid.import_kw) < 1e-6


def test_realtime_price_overrides_current_slot() -> None:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    config = _make_config()
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.timestep_minutes)
    slot1_start = slot_end
    slot1_end = slot1_start + timedelta(minutes=config.ems.timestep_minutes)

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

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    assert plan.timesteps[0].economics.price_import == 0.3
    assert plan.timesteps[1].economics.price_import == 0.2


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
        realtime_load_power=HomeAssistantPowerKwEntitySource(type="home_assistant", entity="load"),
        forecast=load_forecast,
    )
    config = _make_config(
        load=load,
        timestep_minutes=interval_duration,
        min_horizon_minutes=interval_duration * 3,
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

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    timesteps = plan.timesteps
    assert abs(timesteps[0].loads.base_kw - 9.0) < 1e-6
    assert abs(timesteps[1].loads.base_kw - 2.0) < 1e-6
    assert abs(timesteps[2].loads.base_kw - 3.0) < 1e-6


def test_pv_forecast_reused_per_inverter() -> None:
    now = datetime(2025, 12, 27, 8, 2, tzinfo=UTC)
    inverters = [
        InverterConfig(
            id="a",
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
            id="b",
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
    slot_end = slot0 + timedelta(minutes=config.ems.timestep_minutes)
    pv_intervals = [
        PowerForecastInterval(start=slot0, end=slot_end, value=1.5),
        PowerForecastInterval(
            start=slot_end,
            end=slot_end + timedelta(minutes=config.ems.timestep_minutes),
            value=1.5,
        ),
    ]
    price_import_intervals = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.1),
        PriceForecastInterval(
            start=slot_end,
            end=slot_end + timedelta(minutes=config.ems.timestep_minutes),
            value=0.1,
        ),
    ]
    price_export_intervals = [
        PriceForecastInterval(start=slot0, end=slot_end, value=0.0),
        PriceForecastInterval(
            start=slot_end,
            end=slot_end + timedelta(minutes=config.ems.timestep_minutes),
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

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    step = plan.timesteps[0]
    assert step.inverters["a"].pv_kw is not None
    assert step.inverters["b"].pv_kw is not None
    assert abs(step.inverters["a"].pv_kw - 1.5) < 1e-6
    assert abs(step.inverters["b"].pv_kw - 1.5) < 1e-6
    pv_total = sum(inv.pv_kw or 0.0 for inv in step.inverters.values())
    assert abs(pv_total - 3.0) < 1e-6
    assert abs(step.grid.export_kw - 3.0) < 1e-6


def test_load_aware_curtailment_blocks_export() -> None:
    now = datetime(2025, 12, 27, 9, 2, tzinfo=UTC)
    inverter = InverterConfig(
        id="curtail",
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
    config = _make_config(
        inverters=[inverter],
        min_horizon_minutes=5,
    )
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.timestep_minutes)
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

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    step = plan.timesteps[0]
    assert step.inverters["curtail"].curtailment is True
    assert step.inverters["curtail"].pv_curtail_kw is not None
    assert abs(step.inverters["curtail"].pv_curtail_kw - 1.5) < 1e-6
    assert abs(step.grid.export_kw) < 1e-6
    assert abs(step.grid.import_kw) < 1e-6


def test_horizon_uses_shortest_forecast() -> None:
    now = datetime(2025, 12, 27, 10, 2, tzinfo=UTC)
    config = _make_config(min_horizon_minutes=10)
    interval_minutes = config.ems.timestep_minutes
    start = now.replace(minute=0, second=0, microsecond=0)

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": _price_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=3,
                value=0.1,
            ),
            "price_export_forecast": _price_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=2,
                value=0.1,
            ),
        },
        pv_forecasts={
            "pv_forecast": _power_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=4,
                value=1.0,
            )
        },
        load_forecasts={
            "load_forecast": _power_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=5,
                value=0.5,
            )
        },
        realtime_values={
            "load": 0.5,
            "price_import": 0.1,
            "price_export": 0.1,
            "grid": 0.0,
        },
    )

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    assert len(plan.timesteps) == 2


def test_horizon_errors_when_shorter_than_min_horizon_minutes() -> None:
    now = datetime(2025, 12, 27, 10, 2, tzinfo=UTC)
    config = _make_config(min_horizon_minutes=15)
    interval_minutes = config.ems.timestep_minutes
    start = now.replace(minute=0, second=0, microsecond=0)

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": _price_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=3,
                value=0.1,
            ),
            "price_export_forecast": _price_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=2,
                value=0.1,
            ),
        },
        pv_forecasts={
            "pv_forecast": _power_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=3,
                value=1.0,
            )
        },
        load_forecasts={
            "load_forecast": _power_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=3,
                value=0.5,
            )
        },
        realtime_values={
            "load": 0.5,
            "price_import": 0.1,
            "price_export": 0.1,
            "grid": 0.0,
        },
    )

    with pytest.raises(ValueError, match="min_horizon_minutes"):
        EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)


def test_min_horizon_minutes_uses_high_res_interval() -> None:
    now = datetime(2025, 12, 27, 10, 2, tzinfo=UTC)
    config = _make_config(
        timestep_minutes=30,
        min_horizon_minutes=15,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=20,
    )
    interval_minutes = config.ems.high_res_timestep_minutes or config.ems.timestep_minutes
    start = now.replace(
        minute=(now.minute // interval_minutes) * interval_minutes,
        second=0,
        microsecond=0,
    )

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": _price_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=2,
                value=0.1,
            ),
            "price_export_forecast": _price_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=2,
                value=0.1,
            ),
        },
        pv_forecasts={
            "pv_forecast": _power_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=2,
                value=1.0,
            )
        },
        load_forecasts={
            "load_forecast": _power_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=2,
                value=0.5,
            )
        },
        realtime_values={
            "load": 0.5,
            "price_import": 0.1,
            "price_export": 0.1,
            "grid": 0.0,
        },
    )

    with pytest.raises(ValueError, match="min_horizon_minutes"):
        EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)


def test_variable_horizon_averages_into_coarse_slot() -> None:
    now = datetime(2025, 12, 27, 0, 2, tzinfo=UTC)
    config = _make_config(
        timestep_minutes=30,
        min_horizon_minutes=60,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=20,
    )
    interval_minutes = config.ems.high_res_timestep_minutes or config.ems.timestep_minutes
    start = now.replace(
        minute=(now.minute // interval_minutes) * interval_minutes,
        second=0,
        microsecond=0,
    )
    price_import_intervals = [
        PriceForecastInterval(
            start=start + timedelta(minutes=interval_minutes * idx),
            end=start + timedelta(minutes=interval_minutes * (idx + 1)),
            value=float(idx + 1),
        )
        for idx in range(12)
    ]

    resolver = DummyResolver(
        price_forecasts={
            "price_import_forecast": price_import_intervals,
            "price_export_forecast": _price_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=12,
                value=0.0,
            ),
        },
        pv_forecasts={
            "pv_forecast": _power_intervals(
                start,
                interval_minutes=interval_minutes,
                num_intervals=12,
                value=0.0,
            )
        },
        load_forecasts={"load_forecast": _load_intervals(now, config, value=0.5)},
        realtime_values={
            "load": 0.5,
            "price_import": 1.0,
            "price_export": 0.0,
            "grid": 0.0,
        },
    )

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    durations = [step.duration_s for step in plan.timesteps]
    assert durations == [300.0] * 6 + [1800.0]
    assert plan.timesteps[-1].economics.price_import == pytest.approx(9.5)  # type: ignore[reportUnknownMemberType]


def test_realtime_pv_allows_missing_first_forecast_slot() -> None:
    now = datetime(2025, 12, 27, 7, 2, tzinfo=UTC)
    inverter = InverterConfig(
        id="inv",
        name="Inv",
        peak_power_kw=5.0,
        curtailment=None,
        pv=PvConfig(
            realtime_power=HomeAssistantPowerKwEntitySource(
                type="home_assistant",
                entity="pv_realtime",
            ),
            forecast=HomeAssistantSolcastForecastSource(
                type="home_assistant",
                platform="solcast",
                entities=["pv_forecast"],
            ),
        ),
        battery=None,
    )
    config = _make_config(
        inverters=[inverter],
        timestep_minutes=5,
        min_horizon_minutes=10,
    )
    interval_minutes = config.ems.timestep_minutes
    start = now.replace(minute=0, second=0, microsecond=0)

    pv_intervals = _power_intervals(
        start + timedelta(minutes=interval_minutes),
        interval_minutes=interval_minutes,
        num_intervals=1,
        value=1.0,
    )
    price_import_intervals = _price_intervals(
        start,
        interval_minutes=interval_minutes,
        num_intervals=2,
        value=0.1,
    )
    price_export_intervals = _price_intervals(
        start,
        interval_minutes=interval_minutes,
        num_intervals=2,
        value=0.05,
    )

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
            "price_export": 0.05,
            "grid": 0.0,
            "pv_realtime": 2.5,
        },
    )

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    assert len(plan.timesteps) == 2
    assert plan.timesteps[0].inverters["inv"].pv_kw == pytest.approx(2.5)  # type: ignore[reportUnknownMemberType]
    assert plan.timesteps[1].inverters["inv"].pv_kw == pytest.approx(1.0)  # type: ignore[reportUnknownMemberType]


def test_load_aware_curtailment_active_with_negative_price_without_export() -> None:
    now = datetime(2025, 12, 27, 9, 2, tzinfo=UTC)
    inverter = InverterConfig(
        id="curtail",
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
    config = _make_config(
        inverters=[inverter],
        min_horizon_minutes=5,
    )
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.timestep_minutes)
    pv_intervals = [PowerForecastInterval(start=slot0, end=slot_end, value=0.4)]
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

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    step = plan.timesteps[0]
    # Curtailment is not required when PV < load; the key outcome is that export is blocked.
    assert abs(step.grid.export_kw) < 1e-6
    assert abs(step.grid.import_kw - 0.1) < 1e-6


def test_binary_curtailment_prefers_import_over_negative_export() -> None:
    now = datetime(2025, 12, 27, 9, 2, tzinfo=UTC)
    inverter = InverterConfig(
        id="curtail",
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
    config = _make_config(
        inverters=[inverter],
        min_horizon_minutes=5,
    )
    slot0 = now.replace(minute=0, second=0, microsecond=0)
    slot_end = slot0 + timedelta(minutes=config.ems.timestep_minutes)
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

    plan = EmsMilpPlanner(config, resolver=resolver).generate_ems_plan(now=now)
    step = plan.timesteps[0]
    assert step.inverters["curtail"].curtailment is True
    assert abs(step.grid.export_kw) < 1e-6
    assert abs(step.grid.import_kw - 0.5) < 1e-6
