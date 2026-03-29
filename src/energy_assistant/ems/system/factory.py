from __future__ import annotations

import logging

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import HorizonShape, build_horizon_shape
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import AppConfig
from energy_assistant.models.loads import ControlledEvLoad

logger = logging.getLogger(__name__)


class EmsSystemFactory:
    """Builds persistent EMS component definitions."""

    def __init__(self, app_config: AppConfig, *, resolver: ValueResolver) -> None:
        self._app_config = app_config
        self._resolver = resolver
        self._horizon_shape = build_horizon_shape(
            timestep_minutes=app_config.ems.timestep_minutes,
            horizon_minutes=app_config.ems.horizon_minutes,
            high_res_timestep_minutes=app_config.ems.high_res_timestep_minutes,
            high_res_horizon_minutes=app_config.ems.high_res_horizon_minutes,
        )
        self._system = self._build_system()

    def mark_for_hydration(self) -> None:
        self._system.mark_for_hydration(self._resolver)

    @property
    def horizon_shape(self) -> HorizonShape:
        return self._horizon_shape

    @property
    def system(self) -> EmsSystem:
        return self._system

    def _build_system(self) -> EmsSystem:
        switchboard = SwitchboardComponent()

        base_load = BaseLoadComponent(
            bus_id=switchboard.bus_id,
            load=self._app_config.plant.load,
        )

        grid = GridComponent(
            bus_id=switchboard.bus_id,
            grid=self._app_config.plant.grid,
        )

        inverters: dict[str, InverterComponent] = {}
        for inv_cfg in self._app_config.plant.inverters:
            inv_id = inv_cfg.id
            inverters[inv_id] = InverterComponent(
                switchboard_bus_id=switchboard.bus_id,
                inverter=inv_cfg,
                grid_cfg=self._app_config.plant.grid,
                terminal_soc=self._app_config.ems.terminal_soc,
            )

        evs: dict[str, EvComponent] = {}
        for load in self._app_config.loads:
            if not isinstance(load, ControlledEvLoad):
                continue
            evs[load.id] = EvComponent(
                switchboard_bus_id=switchboard.bus_id,
                load=load,
                grid_price_bias_pct=float(self._app_config.plant.grid.grid_price_bias_pct),
            )

        return EmsSystem(
            switchboard=switchboard,
            base_load=base_load,
            grid=grid,
            inverters=inverters,
            evs=evs,
        )
