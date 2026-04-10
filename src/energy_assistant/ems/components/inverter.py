from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.intent import build_inverter_intent
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import (
    InverterComponentPlan,
)
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.series import interval_series_points
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.topology import ComponentTopology, GraphBuildContext, PlanContext
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    InputReference,
    InverterComponentConfig,
)

from .battery import BatteryComponent, BatterySolveState
from .grid import GridComponent
from .pv import PvComponent, PvSolveState


@dataclass(frozen=True, slots=True)
class InverterSolveState:
    inverter_connection: Connection
    battery_solve_states: dict[str, BatterySolveState]
    pv_solve_states: dict[str, PvSolveState]


class InverterComponent(EmsComponent[InverterSolveState, InverterComponentPlan]):
    def __init__(
        self,
        *,
        component_id: str,
        switchboard_bus_id: str,
        inverter: InverterComponentConfig,
        battery_cfgs: dict[str, BatteryComponentConfig] | None = None,
        pvs: dict[str, PvComponent] | None = None,
        batteries: dict[str, BatteryComponent] | None = None,
    ) -> None:
        self.id = str(component_id)
        self.name = str(inverter.name)
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment = inverter.curtailment

        self.ac_bus_id = str(switchboard_bus_id)
        self.dc_bus_id = f"{self.id}_dc"
        self.inverter_link_id = f"{self.id}_acdc"

        self._battery_cfgs = dict(battery_cfgs or {})
        self.pvs = dict(pvs or {})
        self.batteries = dict(batteries or {})

    def set_children(
        self,
        *,
        battery_cfgs: dict[str, BatteryComponentConfig],
        pvs: dict[str, PvComponent],
        batteries: dict[str, BatteryComponent],
    ) -> None:
        self._battery_cfgs = dict(battery_cfgs)
        self.pvs = dict(pvs)
        self.batteries = dict(batteries)

    def describe_topology(self) -> ComponentTopology:
        return ComponentTopology(
            component_id=self.id,
            component_type="inverter",
            connection_target_id=self.ac_bus_id,
        )

    def update_inputs(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
    ) -> None:
        for pv in self.pvs.values():
            pv.update_inputs(horizon=horizon, inputs=inputs)
        for battery in self.batteries.values():
            battery.update_inputs(horizon=horizon, inputs=inputs)

    def build_graph(
        self,
        *,
        horizon: Horizon,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], InverterSolveState]:
        _ = build_ctx
        inverter_connection = Connection(
            horizon=horizon,
            id=self.inverter_link_id,
            a_node_id=self.dc_bus_id,
            b_node_id=self.ac_bus_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=self.peak_power_kw,
                    max_b_to_a_kw=self.peak_power_kw,
                    exclusive=True,
                ),
            },
        )
        elements: list[GraphElement] = [
            Node(
                horizon=horizon,
                id=self.dc_bus_id,
                name=f"DC Bus {self.id}",
                node_role="bus",
            ),
            inverter_connection,
        ]
        solve_state = InverterSolveState(
            inverter_connection=inverter_connection,
            battery_solve_states={},
            pv_solve_states={},
        )
        return elements, solve_state

    def graph_elements(
        self,
        *,
        horizon: Horizon,
        grid_connection: Connection,
        price_import_raw: list[float],
    ) -> tuple[list[GraphElement], InverterSolveState]:
        elements: list[GraphElement] = [
            Node(
                horizon=horizon,
                id=self.dc_bus_id,
                name=f"DC Bus {self.id}",
                node_role="bus",
            )
        ]

        inverter_connection = Connection(
            horizon=horizon,
            id=self.inverter_link_id,
            a_node_id=self.dc_bus_id,
            b_node_id=self.ac_bus_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=self.peak_power_kw,
                    max_b_to_a_kw=self.peak_power_kw,
                    exclusive=True,
                ),
            },
        )
        elements.append(inverter_connection)

        pv_solve_states: dict[str, PvSolveState] = {}
        for pv_id, pv in self.pvs.items():
            pv_elements, pv_solve_state = pv.graph_elements(horizon=horizon)
            elements.extend(pv_elements)
            pv_solve_states[pv_id] = pv_solve_state

        battery_solve_states: dict[str, BatterySolveState] = {}
        for battery_id, battery in self.batteries.items():
            battery_elements, battery_solve_state = battery.graph_elements(
                horizon=horizon,
                grid_connection=grid_connection,
                price_import_raw=price_import_raw,
            )
            elements.extend(battery_elements)
            battery_solve_states[battery_id] = battery_solve_state

        solve_state = InverterSolveState(
            inverter_connection=inverter_connection,
            battery_solve_states=battery_solve_states,
            pv_solve_states=pv_solve_states,
        )
        return elements, solve_state

    def build_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: InverterSolveState,
        plan_ctx: PlanContext,
    ) -> InverterComponentPlan:
        horizon = snapshot.ctx.horizon
        inverter_connection = solve_state.inverter_connection
        ac_net_kw = [
            value_of(inverter_connection.flow_into_node(self.ac_bus_id).get(t))
            - value_of(inverter_connection.flow_out_of_node(self.ac_bus_id).get(t))
            for t in horizon.T
        ]

        battery_charge_kw = [0.0 for _ in horizon.T]
        battery_discharge_kw = [0.0 for _ in horizon.T]
        battery_soc_pct: list[float] = []

        child_batteries = [
            cast(BatteryComponent, plan_ctx.components[child_id])
            for child_id in plan_ctx.children_of(self.id)
            if child_id in plan_ctx.components
            and isinstance(plan_ctx.components[child_id], BatteryComponent)
        ]
        total_capacity_kwh = sum(battery.capacity_kwh for battery in child_batteries)
        if total_capacity_kwh:
            battery_soc_kwh = [0.0 for _ in horizon.T]
            for battery in child_batteries:
                battery_solve_state = plan_ctx.solve_states.get(battery)
                connection = battery_solve_state.connection
                storage = battery_solve_state.storage
                for index, t in enumerate(horizon.T):
                    battery_charge_kw[index] += value_of(
                        connection.flow_into_node(storage.id).get(t)
                    )
                    battery_discharge_kw[index] += value_of(
                        connection.flow_out_of_node(storage.id).get(t)
                    )
                    battery_soc_kwh[index] += value_of(storage.E_by_i.get(t))

            battery_soc_pct = [
                (float(value) / float(total_capacity_kwh)) * 100.0
                for value in battery_soc_kwh
            ]

        battery_cfg = aggregate_battery_for_intent(self._battery_cfgs, self.batteries)
        grid_import_kw = 0.0
        grid_export_kw = 0.0
        grid_price_export = 0.0
        export_limit_normal_kw = 0.0
        first_grid_component = next(
            (
                component
                for component in plan_ctx.components.values()
                if isinstance(component, GridComponent)
            ),
            None,
        )
        if first_grid_component is not None:
            grid_solve_state = plan_ctx.solve_states.get(first_grid_component)
            grid_import_kw_series = [
                value_of(grid_solve_state.connection.flow_into_node(first_grid_component.bus_id).get(t))
                for t in horizon.T
            ]
            grid_export_kw_series = [
                value_of(grid_solve_state.connection.flow_out_of_node(first_grid_component.bus_id).get(t))
                for t in horizon.T
            ]
            grid_import_kw = float(grid_import_kw_series[0]) if grid_import_kw_series else 0.0
            grid_export_kw = float(grid_export_kw_series[0]) if grid_export_kw_series else 0.0
            grid_price_export = (
                float(grid_solve_state.price_export_raw[0])
                if grid_solve_state.price_export_raw
                else 0.0
            )
            export_limit_normal_kw = first_grid_component.max_export_kw

        return InverterComponentPlan(
            ac_net_kw=interval_series_points(horizon, ac_net_kw),
            intent=build_inverter_intent(
                ac_net_kw=float(ac_net_kw[0]) if ac_net_kw else 0.0,
                charge_kw=float(battery_charge_kw[0]) if battery_charge_kw else 0.0,
                discharge_kw=float(battery_discharge_kw[0]) if battery_discharge_kw else 0.0,
                battery_soc_pct=float(battery_soc_pct[0]) if battery_soc_pct else None,
                grid_import_kw=grid_import_kw,
                grid_export_kw=grid_export_kw,
                price_export=grid_price_export,
                export_limit_normal_kw=export_limit_normal_kw,
                battery=battery_cfg,
            ),
        )

    def build_component_plans(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: InverterSolveState,
        plan_ctx: PlanContext,
    ) -> dict[str, InverterComponentPlan]:
        return {
            self.id: self.build_plan(
                snapshot,
                solve_state=solve_state,
                plan_ctx=plan_ctx,
            )
        }


def aggregate_battery_for_intent(
    battery_cfgs: dict[str, BatteryComponentConfig],
    batteries: dict[str, BatteryComponent],
) -> BatteryComponentConfig | None:
    if not battery_cfgs:
        return None

    total_capacity_kwh = sum(
        float(battery.capacity_kwh) for battery in battery_cfgs.values()
    )
    total_reserve_kwh = sum(
        float(battery.capacity_kwh) * float(battery.reserve_soc_pct) / 100.0
        for battery in battery_cfgs.values()
    )

    max_charge_kw = sum(float(battery.max_charge_kw) for battery in batteries.values())
    max_discharge_kw = sum(float(battery.max_discharge_kw) for battery in batteries.values())

    return BatteryComponentConfig(
        type="battery",
        connection=next(iter(battery_cfgs.values())).connection,
        name=" / ".join(battery.name for battery in battery_cfgs.values()),
        capacity_kwh=total_capacity_kwh,
        storage_efficiency_pct=100.0,
        charge_cost_per_kwh=0.0,
        discharge_cost_per_kwh=0.0,
        min_soc_pct=0.0,
        max_soc_pct=min(float(battery.max_soc_pct) for battery in battery_cfgs.values()),
        reserve_soc_pct=(
            (float(total_reserve_kwh) / float(total_capacity_kwh)) * 100.0
            if total_capacity_kwh
            else 0.0
        ),
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        state_of_charge_pct=InputReference(source="aggregate_battery_soc"),
        realtime_power=InputReference(source="aggregate_battery_power"),
    )
