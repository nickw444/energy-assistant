from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from energy_assistant.ems.components.component import EmsComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import ComponentPlan
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.graph import EnergyGraph

_COMPONENT_PLAN_ADAPTER: TypeAdapter[ComponentPlan] = TypeAdapter(ComponentPlan)


class EmsSystem:
    """Persistent logical component registry used to build per-solve model snapshots."""

    def __init__(
        self,
        *,
        components: dict[str, Any],
        ordered_components: tuple[EmsComponent[Any, Any], ...],
    ) -> None:
        self.components = dict(components)
        self.ordered_components = ordered_components

    def build_snapshot(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
    ) -> tuple[ModelSnapshot, SolveStateStore]:
        """Create the solve-scoped graph, MILP snapshot, and component solve-state store."""
        graph = EnergyGraph()
        solve_states = SolveStateStore()
        build_ctx = GraphBuildContext(
            components=self.components,
            solve_states=solve_states,
        )

        for component in self.ordered_components:
            elements, component_solve_state = component.create_graph_elements(
                horizon=horizon,
                inputs=inputs,
                build_ctx=build_ctx,
            )
            graph.add_elements(elements)
            solve_states.put(component, component_solve_state)
            build_ctx.register(component.id, elements)

        for component in self.ordered_components:
            extra_elements = component.create_graph_fragments(
                graph=graph,
                build_ctx=build_ctx,
                solve_states=solve_states,
            )
            graph.add_elements(extra_elements)

        ctx = ModelContext(horizon=horizon)
        return ModelSnapshot(ctx=ctx, graph=graph), solve_states

    def build_component_plans(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: SolveStateStore,
    ) -> dict[str, ComponentPlan]:
        """Extract and normalize the flat component-plan export after the model is solved."""
        plan_ctx = PlanContext(
            components=self.components,
            solve_states=solve_state,
        )

        component_plans: dict[str, ComponentPlan] = {}
        for component in self.ordered_components:
            component_solve_state = solve_state.get(component)
            plan = component.extract_plan(
                snapshot,
                solve_state=component_solve_state,
                plan_ctx=plan_ctx,
            )
            component_plans[component.id] = _COMPONENT_PLAN_ADAPTER.validate_python(plan)
        return component_plans
