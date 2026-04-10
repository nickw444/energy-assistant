from __future__ import annotations

from dataclasses import dataclass

from energy_assistant.ems.components.base_load import BaseLoadComponent, BaseLoadSolveState
from energy_assistant.ems.components.ev import EvComponent, EvSolveState
from energy_assistant.ems.components.grid import GridComponent, GridSolveState
from energy_assistant.ems.components.inverter import InverterComponent, InverterSolveState
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import (
    ComponentPlan,
)
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.topology.graph import EnergyGraph


class EmsSystem:
    """Persistent EMS component definitions with per-solve resolved inputs."""

    def __init__(
        self,
        *,
        switchboard: SwitchboardComponent,
        base_load: BaseLoadComponent,
        grid: GridComponent,
        inverters: dict[str, InverterComponent],
        evs: dict[str, EvComponent],
    ) -> None:
        self.switchboard = switchboard
        self.base_load = base_load
        self.grid = grid
        self.inverters = dict(inverters)
        self.evs = dict(evs)

    @property
    def switchboard_bus_id(self) -> str:
        return str(self.switchboard.bus_id)

    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        self.base_load.update_inputs(horizon=horizon, inputs=inputs)
        self.grid.update_inputs(horizon=horizon, inputs=inputs)
        for inv in self.inverters.values():
            inv.update_inputs(horizon=horizon, inputs=inputs)
        for ev in self.evs.values():
            ev.update_inputs(horizon=horizon, inputs=inputs)

    def build_snapshot(self, *, horizon: Horizon) -> tuple[ModelSnapshot, EmsSystemSolveState]:
        graph = EnergyGraph()
        graph.add_elements(self.switchboard.graph_elements(horizon=horizon))
        base_load_elements, base_load_solve_state = self.base_load.graph_elements(horizon=horizon)
        graph.add_elements(base_load_elements)
        grid_elements, grid_solve_state = self.grid.graph_elements(horizon=horizon)
        graph.add_elements(grid_elements)

        grid_connection = grid_solve_state.connection
        price_import_raw = grid_solve_state.price_import_raw

        inverter_solve_states: dict[str, InverterSolveState] = {}
        for inv in self.inverters.values():
            inverter_elements, inverter_solve_state = inv.graph_elements(
                horizon=horizon,
                grid_connection=grid_connection,
                price_import_raw=price_import_raw,
            )
            graph.add_elements(inverter_elements)
            inverter_solve_states[inv.id] = inverter_solve_state

        ev_solve_states: dict[str, EvSolveState] = {}
        for ev in self.evs.values():
            ev_elements, ev_solve_state = ev.graph_elements(horizon=horizon)
            graph.add_elements(ev_elements)
            ev_solve_states[ev.id] = ev_solve_state

        ctx = ModelContext(horizon=horizon)
        return ModelSnapshot(ctx=ctx, graph=graph), EmsSystemSolveState(
            base_load=base_load_solve_state,
            grid=grid_solve_state,
            inverters=inverter_solve_states,
            evs=ev_solve_states,
        )

    def build_component_plans(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: EmsSystemSolveState,
    ) -> dict[str, ComponentPlan]:
        grid_plan = self.grid.build_plan(snapshot, solve_state=solve_state.grid)
        grid_import_kw = [float(point.value) for point in grid_plan.import_kw]
        grid_export_kw = [float(point.value) for point in grid_plan.export_kw]
        grid_price_export = [float(point.value) for point in grid_plan.price_export_raw]
        export_limit_normal_kw = self.grid.max_export_kw

        component_plans: dict[str, ComponentPlan] = {
            self.switchboard.id: self.switchboard.build_plan(),
            self.base_load.id: self.base_load.build_plan(
                snapshot.ctx.horizon,
                solve_state=solve_state.base_load,
            ),
            self.grid.id: grid_plan,
        }
        for inverter_id, inverter in self.inverters.items():
            component_plans.update(
                inverter.build_component_plans(
                    snapshot,
                    solve_state=solve_state.inverters[inverter_id],
                    grid_import_kw=grid_import_kw,
                    grid_export_kw=grid_export_kw,
                    grid_price_export=grid_price_export,
                    export_limit_normal_kw=export_limit_normal_kw,
                )
            )
        for ev_id, ev in self.evs.items():
            component_plans[ev_id] = ev.build_plan(
                snapshot,
                solve_state=solve_state.evs[ev_id],
            )
        return component_plans


@dataclass(frozen=True, slots=True)
class EmsSystemSolveState:
    base_load: BaseLoadSolveState
    grid: GridSolveState
    inverters: dict[str, InverterSolveState]
    evs: dict[str, EvSolveState]
