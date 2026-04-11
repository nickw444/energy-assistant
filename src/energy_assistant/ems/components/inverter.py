from __future__ import annotations

from dataclasses import dataclass

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

from .battery import BatteryComponent
from .grid import GridComponent


@dataclass(frozen=True, slots=True)
class InverterSolveState:
    inverter_connection: Connection


@dataclass(frozen=True, slots=True)
class BatteryIntentSummary:
    connection: str
    name: str
    capacity_kwh: float
    reserve_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    max_soc_pct: float


class InverterComponent(EmsComponent[InverterSolveState, InverterComponentPlan]):
    def __init__(
        self,
        *,
        component_id: str,
        switchboard_bus_id: str,
        inverter: InverterComponentConfig,
    ) -> None:
        self.id = str(component_id)
        self.name = str(inverter.name)
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment = inverter.curtailment

        self.ac_bus_id = str(switchboard_bus_id)
        self.dc_bus_id = f"{self.id}_dc"
        self.inverter_link_id = f"{self.id}_acdc"

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
        _ = horizon, inputs

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

        child_batteries: list[BatteryComponent] = []
        for child_id in plan_ctx.children_of(self.id):
            component = plan_ctx.components.get(child_id)
            if isinstance(component, BatteryComponent):
                child_batteries.append(component)
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

        battery_cfg = aggregate_battery_for_intent(
            [_battery_intent_summary(battery) for battery in child_batteries]
        )
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
                value_of(
                    grid_solve_state.connection.flow_into_node(first_grid_component.bus_id).get(t)
                )
                for t in horizon.T
            ]
            grid_export_kw_series = [
                value_of(
                    grid_solve_state.connection.flow_out_of_node(first_grid_component.bus_id).get(t)
                )
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


def _battery_intent_summary(battery: BatteryComponent) -> BatteryIntentSummary:
    return BatteryIntentSummary(
        connection=battery.battery_config.connection,
        name=battery.name,
        capacity_kwh=float(battery.capacity_kwh),
        reserve_kwh=float(battery.reserve_kwh),
        max_charge_kw=float(battery.max_charge_kw),
        max_discharge_kw=float(battery.max_discharge_kw),
        max_soc_pct=float(battery.battery_config.max_soc_pct),
    )


def aggregate_battery_for_intent(
    batteries: list[BatteryIntentSummary],
) -> BatteryComponentConfig | None:
    if not batteries:
        return None

    total_capacity_kwh = sum(battery.capacity_kwh for battery in batteries)
    total_reserve_kwh = sum(battery.reserve_kwh for battery in batteries)

    max_charge_kw = sum(battery.max_charge_kw for battery in batteries)
    max_discharge_kw = sum(battery.max_discharge_kw for battery in batteries)

    return BatteryComponentConfig(
        type="battery",
        connection=batteries[0].connection,
        name=" / ".join(battery.name for battery in batteries),
        capacity_kwh=total_capacity_kwh,
        storage_efficiency_pct=100.0,
        charge_cost_per_kwh=0.0,
        discharge_cost_per_kwh=0.0,
        min_soc_pct=0.0,
        max_soc_pct=min(battery.max_soc_pct for battery in batteries),
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
