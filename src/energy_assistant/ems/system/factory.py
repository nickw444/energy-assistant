from __future__ import annotations

import logging
from datetime import datetime

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import AppConfig
from energy_assistant.models.loads import ControlledEvLoad

logger = logging.getLogger(__name__)


class EmsSystemFactory:
    """Wires Layer 1 components into a persistent EmsSystem + topology graph."""

    def __init__(self, app_config: AppConfig, *, resolver: ValueResolver) -> None:
        self._app_config = app_config
        self._resolver = resolver
        self._system = self._build_system()

    @property
    def system(self) -> EmsSystem:
        return self._system

    def mark_for_hydration(self) -> None:
        self._system.mark_for_hydration(self._resolver)

    def forecast_coverage_intervals(self, *, now: datetime, interval_minutes: int) -> int:
        return int(
            self._system.forecast_coverage_intervals(
                now=now,
                interval_minutes=interval_minutes,
                resolver=self._resolver,
            )
        )

    def update_for_run(self, *, horizon: Horizon) -> None:
        self._system.update(horizon=horizon, resolver=self._resolver)

    def _build_system(self) -> EmsSystem:
        graph = EnergyGraph()

        # Layer 1 components build the hidden topology.
        switchboard = SwitchboardComponent(graph=graph)

        base_load = BaseLoadComponent(
            graph=graph,
            bus_id=switchboard.bus_id,
            load=self._app_config.plant.load,
        )

        grid = GridComponent(
            graph=graph,
            bus_id=switchboard.bus_id,
            grid=self._app_config.plant.grid,
        )

        inverters: dict[str, InverterComponent] = {}
        for inv_cfg in self._app_config.plant.inverters:
            inv_id = inv_cfg.id
            inverters[inv_id] = InverterComponent(
                graph=graph,
                switchboard_bus_id=switchboard.bus_id,
                inverter=inv_cfg,
                grid_connection=grid.connection,
                grid_cfg=self._app_config.plant.grid,
                terminal_soc=self._app_config.ems.terminal_soc,
                price_import_raw=grid.price_import_raw,
            )

        evs: dict[str, EvComponent] = {}
        for load in self._app_config.loads:
            if not isinstance(load, ControlledEvLoad):
                continue
            evs[load.id] = EvComponent(
                graph=graph,
                switchboard_bus_id=switchboard.bus_id,
                load=load,
                grid_price_bias_pct=float(self._app_config.plant.grid.grid_price_bias_pct),
            )

        return EmsSystem(
            graph=graph,
            switchboard_bus_id=switchboard.bus_id,
            base_load=base_load,
            grid=grid,
            inverters=inverters,
            evs=evs,
        )
