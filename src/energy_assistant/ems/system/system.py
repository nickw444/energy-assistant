from __future__ import annotations

from typing import Any, cast

from energy_assistant.ems.components.battery import BatteryComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.pv import PvComponent
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import ComponentPlan
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.state import EmsSystemSolveState, SolveStateStore
from energy_assistant.ems.system.topology import GraphBuildContext, PlanContext, PlantTopology
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.models.plant import BatteryComponentConfig


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

        self._inverter_child_ids = self._wire_inverter_children()

    @property
    def inverters(self) -> dict[str, InverterComponent]:
        return {
            component_id: cast(InverterComponent, self.components[component_id])
            for component_id in self.topology.component_ids_of_type("inverter")
            if isinstance(self.components[component_id], InverterComponent)
        }

    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        for component_id in self.topology.component_order:
            if component_id in self._inverter_child_ids:
                continue
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
            component_plans[component_id] = cast(
                ComponentPlan,
                component.build_plan(
                    snapshot,
                    solve_state=solve_state.get(component),
                    plan_ctx=plan_ctx,
                ),
            )
        return component_plans

    def _wire_inverter_children(self) -> set[str]:
        child_ids: set[str] = set()
        for inverter_id in self.topology.component_ids_of_type("inverter"):
            inverter = self.components[inverter_id]
            if not isinstance(inverter, InverterComponent):
                continue

            battery_cfgs: dict[str, BatteryComponentConfig] = {}
            pvs: dict[str, PvComponent] = {}
            batteries: dict[str, BatteryComponent] = {}
            for child_id in self.topology.children_of(inverter_id):
                child = self.components[child_id]
                if isinstance(child, BatteryComponent):
                    batteries[child_id] = child
                    battery_cfgs[child_id] = child.battery_config
                    child_ids.add(child_id)
                elif isinstance(child, PvComponent):
                    pvs[child_id] = child
                    child_ids.add(child_id)

            inverter.set_children(
                battery_cfgs=battery_cfgs,
                pvs=pvs,
                batteries=batteries,
            )

        return child_ids
