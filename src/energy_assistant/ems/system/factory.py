from __future__ import annotations

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import HorizonShape, build_horizon_shape
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

    def __init__(self, app_config: AppConfig) -> None:
        self._app_config = app_config
        self._horizon_shape = build_horizon_shape(
            timestep_minutes=app_config.ems.timestep_minutes,
            horizon_minutes=app_config.ems.horizon_minutes,
            high_res_timestep_minutes=app_config.ems.high_res_timestep_minutes,
            high_res_horizon_minutes=app_config.ems.high_res_horizon_minutes,
        )
        self._system = self._build_system()

    @property
    def horizon_shape(self) -> HorizonShape:
        return self._horizon_shape

    @property
    def system(self) -> EmsSystem:
        return self._system

    def _build_system(self) -> EmsSystem:
        switchboard_id, _switchboard_cfg = _single_component(
            self._app_config.plant,
            SwitchboardComponentConfig,
        )
        grid_id, grid_cfg = _single_component(self._app_config.plant, GridComponentConfig)
        base_load_id, base_load_cfg = _single_component(self._app_config.plant, LoadComponentConfig)

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
        )

        batteries_by_inverter = _battery_components_by_connection(self._app_config.plant)
        pv_by_inverter = _pv_components_by_connection(self._app_config.plant)

        inverters: dict[str, InverterComponent] = {}
        for inverter_id, inverter_cfg in _components(
            self._app_config.plant,
            InverterComponentConfig,
        ):
            battery_entry = batteries_by_inverter.get(inverter_id)
            pv_entry = pv_by_inverter.get(inverter_id)
            battery_id = battery_entry[0] if battery_entry is not None else None
            battery_cfg = battery_entry[1] if battery_entry is not None else None
            pv_id = pv_entry[0] if pv_entry is not None else None
            pv_cfg = pv_entry[1] if pv_entry is not None else None
            inverters[inverter_id] = InverterComponent(
                component_id=inverter_id,
                switchboard_bus_id=switchboard.bus_id,
                inverter=inverter_cfg,
                battery_id=battery_id,
                battery=battery_cfg,
                pv_id=pv_id,
                pv=pv_cfg,
                grid_max_export_kw=float(grid_cfg.constraints.max_export_kw),
                terminal_soc=self._app_config.ems.terminal_soc,
            )

        evs: dict[str, EvComponent] = {}
        for ev_id, ev_cfg in _components(self._app_config.plant, ControlledEvComponentConfig):
            evs[ev_id] = EvComponent(
                component_id=ev_id,
                switchboard_bus_id=switchboard.bus_id,
                load=ev_cfg,
                grid_export_bias_pct=grid.price_export_bias_pct(),
            )

        return EmsSystem(
            switchboard=switchboard,
            base_load=base_load,
            grid=grid,
            inverters=inverters,
            evs=evs,
        )


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
) -> dict[str, tuple[str, BatteryComponentConfig]]:
    result: dict[str, tuple[str, BatteryComponentConfig]] = {}
    for key, component in _components(registry, BatteryComponentConfig):
        result[component.connection] = (key, component)
    return result


def _pv_components_by_connection(
    registry: dict[str, PlantComponentConfig],
) -> dict[str, tuple[str, PvComponentConfig]]:
    result: dict[str, tuple[str, PvComponentConfig]] = {}
    for key, component in _components(registry, PvComponentConfig):
        result[component.connection] = (key, component)
    return result
