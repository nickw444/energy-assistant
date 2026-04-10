from __future__ import annotations

from dataclasses import dataclass

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.intent import build_inverter_intent
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import (
    BatteryComponentPlan,
    InverterComponentPlan,
    PvComponentPlan,
)
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.series import interval_series_points
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
from .pv import PvComponent, PvSolveState


@dataclass(frozen=True, slots=True)
class InverterSolveState:
    inverter_connection: Connection
    battery_solve_states: dict[str, BatterySolveState]
    pv_solve_states: dict[str, PvSolveState]


class InverterComponent:
    def __init__(
        self,
        *,
        component_id: str,
        switchboard_bus_id: str,
        inverter: InverterComponentConfig,
        battery_cfgs: dict[str, BatteryComponentConfig],
        pvs: dict[str, PvComponent],
        batteries: dict[str, BatteryComponent],
    ) -> None:
        self.id = str(component_id)
        self.name = str(inverter.name)
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment = inverter.curtailment

        self.ac_bus_id = str(switchboard_bus_id)
        self.dc_bus_id = f"{self.id}_dc"
        self.inverter_link_id = f"{self.id}_acdc"

        self._battery_cfgs = dict(battery_cfgs)
        self.pvs = dict(pvs)
        self.batteries = dict(batteries)

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

    def build_component_plans(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: InverterSolveState,
        grid_import_kw: list[float],
        grid_export_kw: list[float],
        grid_price_export: list[float],
        export_limit_normal_kw: float,
    ) -> dict[str, InverterComponentPlan | PvComponentPlan | BatteryComponentPlan]:
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

        total_capacity_kwh = sum(
            self.batteries[battery_id].capacity_kwh
            for battery_id in solve_state.battery_solve_states
        )
        if total_capacity_kwh:
            battery_soc_kwh = [0.0 for _ in horizon.T]
            for battery_id, battery_solve_state in solve_state.battery_solve_states.items():
                battery = self.batteries[battery_id]
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

        exports: dict[str, InverterComponentPlan | PvComponentPlan | BatteryComponentPlan] = {
            self.id: InverterComponentPlan(
                ac_net_kw=interval_series_points(horizon, ac_net_kw),
                intent=build_inverter_intent(
                    ac_net_kw=float(ac_net_kw[0]) if ac_net_kw else 0.0,
                    charge_kw=float(battery_charge_kw[0]) if battery_charge_kw else 0.0,
                    discharge_kw=float(battery_discharge_kw[0]) if battery_discharge_kw else 0.0,
                    battery_soc_pct=float(battery_soc_pct[0]) if battery_soc_pct else None,
                    grid_import_kw=float(grid_import_kw[0]) if grid_import_kw else 0.0,
                    grid_export_kw=float(grid_export_kw[0]) if grid_export_kw else 0.0,
                    price_export=float(grid_price_export[0]) if grid_price_export else 0.0,
                    export_limit_normal_kw=export_limit_normal_kw,
                    battery=aggregate_battery_for_intent(
                        self._battery_cfgs,
                        self.batteries,
                    ),
                ),
            )
        }

        for pv_id, pv in self.pvs.items():
            pv_solve_state = solve_state.pv_solve_states.get(pv_id)
            if pv_solve_state is None:
                continue
            exports[pv.id] = pv.build_plan(snapshot, solve_state=pv_solve_state)

        for battery_id, battery in self.batteries.items():
            battery_solve_state = solve_state.battery_solve_states.get(battery_id)
            if battery_solve_state is None:
                continue
            exports[battery.id] = battery.build_plan(snapshot, solve_state=battery_solve_state)

        return exports


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
