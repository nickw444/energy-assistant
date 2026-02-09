from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.models import InverterTimestepPlan
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import DirectionalLimit, ExclusiveDirection
from energy_assistant.ems.topology.nodes import BusNodeTemplate
from energy_assistant.models.plant import BatteryConfig, InverterConfig

from .battery import BatteryComponent
from .pv import PvComponent

_CURTAIL_POWER_THRESHOLD_KW = 0.01


class InverterComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraphTemplate,
        switchboard_bus_id: str,
        inverter: InverterConfig,
        grid_connection_id: str,
        grid_max_export_kw: float,
        terminal_soc_mode: Literal["hard", "adaptive"],
        terminal_soc_penalty_per_kwh: float | Literal["mean", "median"] | None,
        battery_time_cost_key: str,
        pv_available_key: str,
        battery_initial_soc_key: str,
        battery_charge_cost_key: str,
        battery_discharge_cost_key: str,
    ) -> None:
        self.id = inverter.id
        self.name = inverter.name
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment = inverter.curtailment

        self.ac_bus_id = str(switchboard_bus_id)
        self.dc_bus_id = f"{self.id}_dc"
        self.inverter_link_id = f"inverter_{self.id}_acdc"

        graph.add_bus(BusNodeTemplate(id=self.dc_bus_id, name=f"DC Bus {self.id}", domain="dc"))
        graph.add_connection(
            ConnectionTemplate(
                id=self.inverter_link_id,
                a_node_id=self.dc_bus_id,
                b_node_id=self.ac_bus_id,
                link_components=[
                    # a_to_b is DC -> AC (inverting), b_to_a is AC -> DC (rectifying)
                    DirectionalLimit(
                        max_a_to_b_kw=self.peak_power_kw,
                        max_b_to_a_kw=self.peak_power_kw,
                    ),
                    ExclusiveDirection(),
                ],
            )
        )

        self.pv = PvComponent(
            graph=graph,
            inverter_id=self.id,
            dc_bus_id=self.dc_bus_id,
            peak_power_kw=self.peak_power_kw,
            curtailment=self.curtailment,
            available_series_key=pv_available_key,
        )

        self.battery: BatteryComponent | None = None
        if inverter.battery is not None:
            self.battery = _build_battery(
                graph=graph,
                inverter_id=self.id,
                dc_bus_id=self.dc_bus_id,
                inverter_peak_kw=self.peak_power_kw,
                battery=inverter.battery,
                grid_connection_id=grid_connection_id,
                grid_max_export_kw=grid_max_export_kw,
                terminal_soc_mode=terminal_soc_mode,
                terminal_soc_penalty_per_kwh=terminal_soc_penalty_per_kwh,
                battery_time_cost_key=battery_time_cost_key,
                battery_initial_soc_key=battery_initial_soc_key,
                battery_charge_cost_key=battery_charge_cost_key,
                battery_discharge_cost_key=battery_discharge_cost_key,
            )

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[InverterTimestepPlan]:
        inv_conn = snapshot.graph.connections[self.inverter_link_id]

        batt_conn = None
        batt_node = None
        batt_capacity = None
        if self.battery is not None:
            batt_conn = snapshot.graph.connections[self.battery.connection_id]
            batt_node = snapshot.graph.storage_nodes[self.battery.node_id]
            batt_capacity = self.battery.capacity_kwh

        for t in snapshot.ctx.horizon.T:
            pv_kw = self.pv.pv_kw(snapshot, t)
            pv_curtail_kw = self.pv.curtail_kw(snapshot, t)
            curtailment_active = (
                None
                if pv_curtail_kw is None
                else bool(float(pv_curtail_kw) > _CURTAIL_POWER_THRESHOLD_KW)
            )
            ac_net = value_of(inv_conn.P_a_to_b.get(t)) - value_of(inv_conn.P_b_to_a.get(t))

            batt_charge_kw = None
            batt_discharge_kw = None
            batt_soc_kwh = None
            batt_soc_pct = None
            if batt_conn is not None and batt_node is not None:
                batt_charge_kw = value_of(batt_conn.P_a_to_b.get(t))
                batt_discharge_kw = value_of(batt_conn.P_b_to_a.get(t))
                batt_soc_kwh = value_of(batt_node.E_by_i.get(t))
                if batt_capacity:
                    batt_soc_pct = (float(batt_soc_kwh) / float(batt_capacity)) * 100.0

            yield InverterTimestepPlan(
                name=str(self.name),
                pv_kw=pv_kw,
                pv_curtail_kw=pv_curtail_kw,
                ac_net_kw=ac_net,
                battery_charge_kw=batt_charge_kw,
                battery_discharge_kw=batt_discharge_kw,
                battery_soc_kwh=batt_soc_kwh,
                battery_soc_pct=batt_soc_pct,
                curtailment=curtailment_active,
            )


def _build_battery(
    *,
    graph: EnergyGraphTemplate,
    inverter_id: str,
    dc_bus_id: str,
    inverter_peak_kw: float,
    battery: BatteryConfig,
    grid_connection_id: str,
    grid_max_export_kw: float,
    terminal_soc_mode: Literal["hard", "adaptive"],
    terminal_soc_penalty_per_kwh: float | Literal["mean", "median"] | None,
    battery_time_cost_key: str,
    battery_initial_soc_key: str,
    battery_charge_cost_key: str,
    battery_discharge_cost_key: str,
) -> BatteryComponent:
    capacity_kwh = float(battery.capacity_kwh)
    charge_limit = (
        float(battery.max_charge_kw)
        if battery.max_charge_kw is not None
        else float(inverter_peak_kw)
    )
    discharge_limit = (
        float(battery.max_discharge_kw)
        if battery.max_discharge_kw is not None
        else float(inverter_peak_kw)
    )
    discharge_limit = min(discharge_limit, float(inverter_peak_kw))

    soc_min_kwh = capacity_kwh * float(battery.min_soc_pct) / 100.0
    soc_max_kwh = capacity_kwh * float(battery.max_soc_pct) / 100.0
    reserve_kwh = capacity_kwh * float(battery.reserve_soc_pct) / 100.0
    eta = float(battery.storage_efficiency_pct) / 100.0

    return BatteryComponent(
        graph=graph,
        inverter_id=inverter_id,
        dc_bus_id=dc_bus_id,
        capacity_kwh=capacity_kwh,
        soc_min_kwh=soc_min_kwh,
        soc_max_kwh=soc_max_kwh,
        reserve_kwh=reserve_kwh,
        storage_efficiency=eta,
        initial_soc_kwh_key=battery_initial_soc_key,
        terminal_mode=terminal_soc_mode,
        terminal_penalty_per_kwh=terminal_soc_penalty_per_kwh,
        terminal_soc_value_per_kwh=battery.soc_value_per_kwh,
        max_charge_kw=charge_limit,
        max_discharge_kw=discharge_limit,
        charge_cost_key=battery_charge_cost_key,
        discharge_cost_key=battery_discharge_cost_key,
        time_cost_key=battery_time_cost_key,
        grid_connection_id=grid_connection_id,
        grid_max_export_kw=grid_max_export_kw,
    )
