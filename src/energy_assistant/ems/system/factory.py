from __future__ import annotations

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.battery import BatteryComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.pv import PvComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.inputs.alignment import (
    PowerForecastAligner,
    PriceForecastAligner,
)
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.planning.horizon import HorizonShape, build_horizon_shape
from energy_assistant.ems.planning.pricing import PriceSeriesBuilder
from energy_assistant.ems.planning.time_windows import TimeWindowMatcher
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.models.config import AppConfig
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    ControlledEvComponentConfig,
    GridComponentConfig,
    InverterComponentConfig,
    LoadComponentConfig,
    PlantComponentConfig,
    PvComponentConfig,
    SwitchboardComponentConfig,
)


class EmsSystemFactory:
    """Builds persistent EMS component definitions."""

    def __init__(
        self,
        *,
        horizon_shape: HorizonShape,
        input_applicator: EmsInputApplicator,
        system: EmsSystem,
    ) -> None:
        self._horizon_shape = horizon_shape
        self._input_applicator = input_applicator
        self._system = system

    @classmethod
    def create(cls, app_config: AppConfig) -> EmsSystemFactory:
        horizon_shape = build_horizon_shape(
            timestep_minutes=app_config.ems.timestep_minutes,
            horizon_minutes=app_config.ems.horizon_minutes,
            high_res_timestep_minutes=app_config.ems.high_res_timestep_minutes,
            high_res_horizon_minutes=app_config.ems.high_res_horizon_minutes,
        )
        input_applicator = EmsInputApplicator(
            input_configs=app_config.inputs,
            power_aligner=PowerForecastAligner(),
            price_aligner=PriceForecastAligner(),
        )
        system = _build_system(app_config)
        return cls(
            horizon_shape=horizon_shape,
            input_applicator=input_applicator,
            system=system,
        )

    @property
    def horizon_shape(self) -> HorizonShape:
        return self._horizon_shape

    @property
    def system(self) -> EmsSystem:
        return self._system

    @property
    def input_applicator(self) -> EmsInputApplicator:
        return self._input_applicator

def _components[TPlant: PlantComponentConfig](
    registry: dict[str, PlantComponentConfig],
    expected_type: type[TPlant],
) -> list[tuple[str, TPlant]]:
    return [
        (key, component)
        for key, component in registry.items()
        if isinstance(component, expected_type)
    ]


def _single_component[TPlant: PlantComponentConfig](
    registry: dict[str, PlantComponentConfig],
    expected_type: type[TPlant],
) -> tuple[str, TPlant]:
    matches = _components(registry, expected_type)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {expected_type.__name__}")
    return matches[0]


def _battery_components_by_connection(
    registry: dict[str, PlantComponentConfig],
) -> dict[str, list[tuple[str, BatteryComponentConfig]]]:
    result: dict[str, list[tuple[str, BatteryComponentConfig]]] = {}
    for key, component in _components(registry, BatteryComponentConfig):
        result.setdefault(component.connection, []).append((key, component))
    return result


def _pv_components_by_connection(
    registry: dict[str, PlantComponentConfig],
) -> dict[str, list[tuple[str, PvComponentConfig]]]:
    result: dict[str, list[tuple[str, PvComponentConfig]]] = {}
    for key, component in _components(registry, PvComponentConfig):
        result.setdefault(component.connection, []).append((key, component))
    return result


def _build_system(app_config: AppConfig) -> EmsSystem:
    switchboard_id, _switchboard_cfg = _single_component(
        app_config.plant,
        SwitchboardComponentConfig,
    )
    grid_id, grid_cfg = _single_component(app_config.plant, GridComponentConfig)
    base_load_id, base_load_cfg = _single_component(app_config.plant, LoadComponentConfig)

    time_window_matcher = TimeWindowMatcher()
    price_series_builder = PriceSeriesBuilder()

    switchboard = SwitchboardComponent(component_id=switchboard_id)
    base_load = BaseLoadComponent(
        bus_id=switchboard.bus_id,
        component_id=base_load_id,
        load=base_load_cfg,
    )
    grid = GridComponent(
        bus_id=switchboard.bus_id,
        component_id=grid_id,
        grid=grid_cfg,
        time_window_matcher=time_window_matcher,
        price_series_builder=price_series_builder,
    )

    batteries_by_inverter = _battery_components_by_connection(app_config.plant)
    pv_by_inverter = _pv_components_by_connection(app_config.plant)

    inverters: dict[str, InverterComponent] = {}
    for inverter_id, inverter_cfg in _components(
        app_config.plant,
        InverterComponentConfig,
    ):
        pvs = {
            pv_id: PvComponent(
                component_id=pv_id,
                inverter_id=inverter_id,
                inverter=inverter_cfg,
                pv=pv,
                dc_bus_id=f"{inverter_id}_dc",
            )
            for pv_id, pv in pv_by_inverter.get(inverter_id, [])
        }
        batteries = {
            battery_id: BatteryComponent(
                component_id=battery_id,
                inverter_id=inverter_id,
                dc_bus_id=f"{inverter_id}_dc",
                inverter_peak_kw=float(inverter_cfg.peak_power_kw),
                battery=battery,
                grid_max_export_kw=float(grid_cfg.constraints.max_export_kw),
            )
            for battery_id, battery in batteries_by_inverter.get(inverter_id, [])
        }
        inverters[inverter_id] = InverterComponent(
            component_id=inverter_id,
            switchboard_bus_id=switchboard.bus_id,
            inverter=inverter_cfg,
            battery_cfgs={
                battery_id: battery
                for battery_id, battery in batteries_by_inverter.get(inverter_id, [])
            },
            pvs=pvs,
            batteries=batteries,
        )

    evs: dict[str, EvComponent] = {}
    for ev_id, ev_cfg in _components(app_config.plant, ControlledEvComponentConfig):
        evs[ev_id] = EvComponent(
            component_id=ev_id,
            switchboard_bus_id=switchboard.bus_id,
            load=ev_cfg,
            grid_export_bias_pct=grid.price_export_bias_pct(),
            time_window_matcher=time_window_matcher,
        )

    return EmsSystem(
        switchboard=switchboard,
        base_load=base_load,
        grid=grid,
        inverters=inverters,
        evs=evs,
    )
