from __future__ import annotations

from typing import Any

from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import ComponentPlan
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.state import EmsSystemSolveState, SolveStateStore
from energy_assistant.ems.system.topology import GraphBuildContext, PlanContext, PlantTopology
from energy_assistant.ems.topology.graph import EnergyGraph


class EmsSystem:
    """Persistent EMS component definitions with per-solve resolved inputs."""

    def __init__(
        self,
        *,
        components: dict[str, EmsComponent[Any, Any]],
        topology: PlantTopology,
    ) -> None:
        self.components = dict(components)
        self.topology = topology

        expected_ids = set(self.topology.component_ids)
        actual_ids = set(self.components)
        if actual_ids != expected_ids:
            missing = sorted(expected_ids - actual_ids)
            extra = sorted(actual_ids - expected_ids)
            raise ValueError(
                "components and topology ids must match exactly; "
                f"missing={missing} extra={extra}"
            )

    @property
    def inverters(self) -> dict[str, InverterComponent]:
        inverters: dict[str, InverterComponent] = {}
        for component_id in self.topology.component_ids_of_type("inverter"):
            component = self.components[component_id]
            if isinstance(component, InverterComponent):
                inverters[component_id] = component
        return inverters

    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        for component_id in self.topology.component_order:
            self.components[component_id].update_inputs(horizon=horizon, inputs=inputs)

    def build_snapshot(self, *, horizon: Horizon) -> tuple[ModelSnapshot, SolveStateStore]:
        graph = EnergyGraph()
        solve_states = SolveStateStore()
        build_ctx = GraphBuildContext(
            topology=self.topology,
            components=self.components,
            solve_states=solve_states,
        )

        for component_id in self.topology.component_order:
            component = self.components[component_id]
            elements, component_solve_state = component.build_graph(
                horizon=horizon,
                build_ctx=build_ctx,
            )
            graph.add_elements(elements)
            solve_states.put(component, component_solve_state)
            build_ctx.register(component_id, elements)

        ctx = ModelContext(horizon=horizon)
        return ModelSnapshot(ctx=ctx, graph=graph), solve_states

    def build_component_plans(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: EmsSystemSolveState,
    ) -> dict[str, ComponentPlan]:
        plan_ctx = PlanContext(
            topology=self.topology,
            components=self.components,
            solve_states=solve_state,
        )

        component_plans: dict[str, ComponentPlan] = {}
        for component_id in self.topology.component_order:
            component = self.components[component_id]
            component_plans[component_id] = self._build_component_plan(
                component,
                snapshot=snapshot,
                solve_state=solve_state.get(component),
                plan_ctx=plan_ctx,
            )
        return component_plans

    def _build_component_plan(
        self,
        component: EmsComponent[Any, Any],
        *,
        snapshot: ModelSnapshot,
        solve_state: Any,
        plan_ctx: PlanContext,
    ) -> ComponentPlan:
        return component.build_plan(
            snapshot,
            solve_state=solve_state,
            plan_ctx=plan_ctx,
        )
