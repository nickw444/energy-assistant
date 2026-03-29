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
from energy_assistant.models.config import TerminalSocConfig
from energy_assistant.models.plant import (
    BatteryComponentConfig,
    InverterComponentConfig,
    PvComponentConfig,
)

from .battery import BatteryComponent, BatterySolveState
from .pv import PvComponent, PvSolveState


@dataclass(frozen=True, slots=True)
class InverterSolveState:
    inverter_connection: Connection
    battery_solve_state: BatterySolveState | None
    pv_solve_state: PvSolveState | None


class InverterComponent:
    def __init__(
        self,
        *,
        component_id: str,
        switchboard_bus_id: str,
        inverter: InverterComponentConfig,
        battery_id: str | None,
        battery: BatteryComponentConfig | None,
        pv_id: str | None,
        pv: PvComponentConfig | None,
        grid_max_export_kw: float,
        terminal_soc: TerminalSocConfig,
    ) -> None:
        self.id = str(component_id)
        self.name = str(inverter.name)
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment = inverter.curtailment
        self._battery_cfg = battery

        self.ac_bus_id = str(switchboard_bus_id)
        self.dc_bus_id = f"{self.id}_dc"
        self.inverter_link_id = f"{self.id}_acdc"

        self.pv: PvComponent | None = None
        if pv is not None:
            self.pv = PvComponent(
                component_id=pv_id or f"{self.id}_pv",
                inverter_id=self.id,
                inverter=inverter,
                pv=pv,
                dc_bus_id=self.dc_bus_id,
            )

        self.battery: BatteryComponent | None = None
        if battery is not None:
            self.battery = BatteryComponent(
                component_id=battery_id or f"{self.id}_battery",
                inverter_id=self.id,
                dc_bus_id=self.dc_bus_id,
                inverter_peak_kw=self.peak_power_kw,
                battery=battery,
                grid_max_export_kw=float(grid_max_export_kw),
                terminal_soc=terminal_soc,
            )

    def update_inputs(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
    ) -> None:
        if self.pv is not None:
            self.pv.update_inputs(horizon=horizon, inputs=inputs)
        if self.battery is not None:
            self.battery.update_inputs(horizon=horizon, inputs=inputs)

    def graph_elements(
        self,
        *,
        horizon: Horizon,
        grid_connection: Connection,
        price_import_raw: list[float],
    ) -> tuple[list[GraphElement], InverterSolveState]:
        elements: list[GraphElement] = []

        elements.append(
            Node(
                horizon=horizon,
                id=self.dc_bus_id,
                name=f"DC Bus {self.id}",
                node_role="bus",
            )
        )

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

        pv_solve_state: PvSolveState | None = None
        if self.pv is not None:
            pv_elements, pv_solve_state = self.pv.graph_elements(horizon=horizon)
            elements.extend(pv_elements)

        battery_solve_state: BatterySolveState | None = None
        if self.battery is not None:
            battery_elements, battery_solve_state = self.battery.graph_elements(
                horizon=horizon,
                grid_connection=grid_connection,
                price_import_raw=price_import_raw,
            )
            elements.extend(battery_elements)

        solve_state = InverterSolveState(
            inverter_connection=inverter_connection,
            battery_solve_state=battery_solve_state,
            pv_solve_state=pv_solve_state,
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
        battery_charge_kw: list[float] = []
        battery_discharge_kw: list[float] = []
        battery_soc_pct: list[float] = []
        if solve_state.battery_solve_state is not None:
            batt_conn = solve_state.battery_solve_state.connection
            batt_node = solve_state.battery_solve_state.storage
            battery_charge_kw = [
                value_of(batt_conn.flow_into_node(batt_node.id).get(t)) for t in horizon.T
            ]
            battery_discharge_kw = [
                value_of(batt_conn.flow_out_of_node(batt_node.id).get(t)) for t in horizon.T
            ]
            battery_soc_pct = [
                (
                    value_of(batt_node.E_by_i.get(t)) / float(self.battery.capacity_kwh) * 100.0
                    if self.battery is not None and self.battery.capacity_kwh
                    else 0.0
                )
                for t in horizon.T
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
                    battery=self._battery_cfg,
                ),
            )
        }
        if self.pv is not None and solve_state.pv_solve_state is not None:
            exports[self.pv.id] = self.pv.build_plan(
                snapshot,
                solve_state=solve_state.pv_solve_state,
            )
        if self.battery is not None and solve_state.battery_solve_state is not None:
            exports[self.battery.id] = self.battery.build_plan(
                snapshot, solve_state=solve_state.battery_solve_state
            )
        return exports
