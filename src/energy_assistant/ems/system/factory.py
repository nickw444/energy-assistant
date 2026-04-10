from __future__ import annotations

from typing import Any

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.battery import BatteryComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.pv import PvComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.planning.horizon import HorizonShape, build_horizon_shape
from energy_assistant.ems.planning.pricing import PriceSeriesBuilder
from energy_assistant.ems.planning.time_windows import TimeWindowMatcher
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.ems.system.topology import PlantTopology
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


def _switchboard_bus(
    switchboards: dict[str, SwitchboardComponent],
    *,
    component_key: str,
    target_key: str,
) -> str:
    try:
        return switchboards[target_key].bus_id
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(
            f"component {component_key} references missing switchboard {target_key!r}"
        ) from exc


def _inverter_config(
    inverters: dict[str, InverterComponentConfig],
    *,
    component_key: str,
    target_key: str,
) -> InverterComponentConfig:
    try:
        return inverters[target_key]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(
            f"component {component_key} references missing inverter {target_key!r}"
        ) from exc


def _inverter_switchboard_id(
    inverters: dict[str, InverterComponentConfig],
    *,
    component_key: str,
    target_key: str,
) -> str:
    return _inverter_config(
        inverters,
        component_key=component_key,
        target_key=target_key,
    ).connection


def _build_system(app_config: AppConfig) -> EmsSystem:
    time_window_matcher = TimeWindowMatcher()
    price_series_builder = PriceSeriesBuilder()

    switchboards = {
        key: SwitchboardComponent(component_id=key)
        for key, _ in _components(app_config.plant, SwitchboardComponentConfig)
    }
    inverter_cfgs = {
        key: component
        for key, component in _components(app_config.plant, InverterComponentConfig)
    }

    grids = {
        key: GridComponent(
            bus_id=_switchboard_bus(
                switchboards,
                component_key=key,
                target_key=component.connection,
            ),
            component_id=key,
            grid=component,
            time_window_matcher=time_window_matcher,
            price_series_builder=price_series_builder,
        )
        for key, component in _components(app_config.plant, GridComponentConfig)
    }
    grids_by_switchboard = _group_grids_by_switchboard(grids)

    loads = {
        key: BaseLoadComponent(
            bus_id=_switchboard_bus(
                switchboards,
                component_key=key,
                target_key=component.connection,
            ),
            component_id=key,
            load=component,
        )
        for key, component in _components(app_config.plant, LoadComponentConfig)
    }

    inverters = {
        key: InverterComponent(
            component_id=key,
            switchboard_bus_id=_switchboard_bus(
                switchboards,
                component_key=key,
                target_key=component.connection,
            ),
            inverter=component,
        )
        for key, component in inverter_cfgs.items()
    }

    pvs = {
        key: PvComponent(
            component_id=key,
            inverter_id=component.connection,
            inverter=_inverter_config(
                inverter_cfgs,
                component_key=key,
                target_key=component.connection,
            ),
            pv=component,
            dc_bus_id=f"{component.connection}_dc",
        )
        for key, component in _components(app_config.plant, PvComponentConfig)
    }

    batteries = {
        key: BatteryComponent(
            component_id=key,
            inverter_id=component.connection,
            dc_bus_id=f"{component.connection}_dc",
            inverter_peak_kw=float(
                _inverter_config(
                    inverter_cfgs,
                    component_key=key,
                    target_key=component.connection,
                ).peak_power_kw
            ),
            battery=component,
            grid_max_export_kw=_grid_max_export_kw(
                grids_by_switchboard.get(
                    _inverter_switchboard_id(
                        inverter_cfgs,
                        component_key=key,
                        target_key=component.connection,
                    ),
                    {},
                )
            ),
        )
        for key, component in _components(app_config.plant, BatteryComponentConfig)
    }

    evs = {
        key: EvComponent(
            component_id=key,
            switchboard_bus_id=_switchboard_bus(
                switchboards,
                component_key=key,
                target_key=component.connection,
            ),
            load=component,
            grid_export_bias_pct=_grid_export_bias_pct(
                grids_by_switchboard.get(component.connection, {})
            ),
            time_window_matcher=time_window_matcher,
        )
        for key, component in _components(app_config.plant, ControlledEvComponentConfig)
    }

    components: dict[str, EmsComponent[Any, Any]] = {}
    components.update(switchboards)
    components.update(grids)
    components.update(loads)
    components.update(inverters)
    components.update(pvs)
    components.update(batteries)
    components.update(evs)

    topology = PlantTopology.from_descriptions(
        [component.describe_topology() for component in components.values()]
    )
    return EmsSystem(components=components, topology=topology)


def _grid_max_export_kw(grids: dict[str, GridComponent]) -> float:
    if not grids:
        return 0.0
    return max(grid.max_export_kw for grid in grids.values())


def _grid_export_bias_pct(grids: dict[str, GridComponent]) -> float:
    if not grids:
        return 0.0
    first_grid_id = next(iter(grids))
    return grids[first_grid_id].price_export_bias_pct()


def _group_grids_by_switchboard(
    grids: dict[str, GridComponent],
) -> dict[str, dict[str, GridComponent]]:
    grouped: dict[str, dict[str, GridComponent]] = {}
    for grid_id, grid in grids.items():
        grouped.setdefault(grid.bus_id, {})[grid_id] = grid
    return grouped
