from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext, ObjectiveTerm
from energy_assistant.ems.models import (
    EconomicsTimestepPlan,
    EvTimestepPlan,
    GridTimestepPlan,
    InverterTimestepPlan,
    LoadsTimestepPlan,
    TimestepPlan,
)
from energy_assistant.ems.topology.graph import EnergyGraphModel, EnergyGraphTemplate

if TYPE_CHECKING:
    from energy_assistant.ems.components.ev import EvComponent
    from energy_assistant.ems.components.grid import GridComponent
    from energy_assistant.ems.components.inverter import InverterComponent


class ModelSnapshot:
    def __init__(self, *, ctx: ModelContext, graph: EnergyGraphModel) -> None:
        self.ctx = ctx
        self.graph = graph

        self.problem = pulp.LpProblem("ems_optimisation", pulp.LpMinimize)

        constraints: list[ConstraintSpec] = []
        objective_terms: list[ObjectiveTerm] = []
        for fragment in graph.fragments:
            constraints.extend(fragment.constraints)
            objective_terms.extend(fragment.objective_terms)

        _attach_constraints(self.problem, constraints)
        self.objective = (
            pulp.lpSum(term.expr for term in objective_terms)
            if objective_terms
            else 0.0
        )
        self.problem += self.objective


class EmsSystem:
    """Persistent EMS system template composed of Layer 1 components + a hidden Layer 0 topology."""

    def __init__(
        self,
        *,
        graph: EnergyGraphTemplate,
        grid: GridComponent,
        inverters: dict[str, InverterComponent],
        evs: dict[str, EvComponent],
    ) -> None:
        self._graph = graph
        self.grid = grid
        self.inverters = dict(inverters)
        self.evs = dict(evs)

    def bind(self, ctx: ModelContext) -> ModelSnapshot:
        graph_model = self._graph.bind(ctx)
        return ModelSnapshot(ctx=ctx, graph=graph_model)

    def build_timestep_plans(self, snapshot: ModelSnapshot) -> list[TimestepPlan]:
        horizon = snapshot.ctx.horizon
        inputs = snapshot.ctx.inputs

        base_load_series = inputs.float_series("base_load_kw")
        price_import = inputs.float_series("price_import_raw")
        price_export = inputs.float_series("price_export_raw")
        price_import_eff = inputs.float_series("price_import_effective")
        price_export_eff = inputs.float_series("price_export_effective")

        grid_iter = self.grid.iter_timestep_plan(snapshot)
        inverter_iters: dict[str, Iterator[InverterTimestepPlan]] = {
            inv_id: inv.iter_timestep_plan(snapshot) for inv_id, inv in self.inverters.items()
        }
        ev_iters: dict[str, Iterator[EvTimestepPlan]] = {
            ev_id: ev.iter_timestep_plan(snapshot) for ev_id, ev in self.evs.items()
        }

        cumulative_cost = 0.0
        timesteps: list[TimestepPlan] = []
        for t, slot in enumerate(horizon.slots):
            grid_plan: GridTimestepPlan = next(grid_iter)

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


def _attach_constraints(problem: pulp.LpProblem, constraints: list[ConstraintSpec]) -> None:
    seen: set[str] = set()
    for spec in constraints:
        name = spec.name
        if name in seen:
            raise ValueError(f"Duplicate constraint name: {name}")
        seen.add(name)
        problem += (spec.constraint, name)
