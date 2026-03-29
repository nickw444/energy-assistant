from __future__ import annotations

from collections.abc import Iterator
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
    EconomicsTimestepPlan,
    EvTimestepPlan,
    GridTimestepPlan,
    InverterTimestepPlan,
    LoadsTimestepPlan,
    TimestepPlan,
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

    def build_timestep_plans(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: EmsSystemSolveState,
    ) -> list[TimestepPlan]:
        horizon = snapshot.ctx.horizon

        base_load_series = solve_state.base_load.base_load_kw
        price_import = solve_state.grid.price_import_raw
        price_export = solve_state.grid.price_export_raw
        price_import_eff = solve_state.grid.price_import_effective
        price_export_eff = solve_state.grid.price_export_effective

        grid_iter: Iterator[GridTimestepPlan] = self.grid.iter_timestep_plan(
            snapshot,
            solve_state=solve_state.grid,
        )
        inverter_iters: dict[str, Iterator[InverterTimestepPlan]] = {
            inv_id: inv.iter_timestep_plan(snapshot, solve_state=solve_state.inverters[inv_id])
            for inv_id, inv in self.inverters.items()
        }
        ev_iters: dict[str, Iterator[EvTimestepPlan]] = {
            ev_id: ev.iter_timestep_plan(snapshot, solve_state=solve_state.evs[ev_id])
            for ev_id, ev in self.evs.items()
        }

        cumulative_cost = 0.0
        timesteps: list[TimestepPlan] = []
        for t, slot in enumerate(horizon.slots):
            grid_plan = next(grid_iter)

            inverter_plans: dict[str, InverterTimestepPlan] = {
                inv_id: next(it) for inv_id, it in sorted(inverter_iters.items())
            }
            ev_plans: dict[str, EvTimestepPlan] = {
                ev_id: next(it) for ev_id, it in sorted(ev_iters.items())
            }

            base_kw = float(base_load_series[t]) if t < len(base_load_series) else 0.0
            ev_kw_total = sum(float(ev.charge_kw) for ev in ev_plans.values())
            total_kw = base_kw + ev_kw_total

            segment_cost = (
                float(grid_plan.import_kw) * float(price_import[t])
                - float(grid_plan.export_kw) * float(price_export[t])
            ) * float(slot.duration_h)
            cumulative_cost += float(segment_cost)

            timesteps.append(
                TimestepPlan(
                    index=t,
                    start=slot.start,
                    end=slot.end,
                    duration_s=(slot.end - slot.start).total_seconds(),
                    grid=grid_plan,
                    inverters=inverter_plans,
                    loads=LoadsTimestepPlan(
                        base_kw=base_kw,
                        evs=ev_plans,
                        total_kw=total_kw,
                    ),
                    economics=EconomicsTimestepPlan(
                        price_import=float(price_import[t]),
                        price_export=float(price_export[t]),
                        price_import_effective=float(price_import_eff[t]),
                        price_export_effective=float(price_export_eff[t]),
                        segment_cost=float(segment_cost),
                        cumulative_cost=float(cumulative_cost),
                    ),
                )
            )
        return timesteps


@dataclass(frozen=True, slots=True)
class EmsSystemSolveState:
    base_load: BaseLoadSolveState
    grid: GridSolveState
    inverters: dict[str, InverterSolveState]
    evs: dict[str, EvSolveState]
