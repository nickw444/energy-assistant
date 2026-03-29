from __future__ import annotations

from collections.abc import Iterator

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.input_registry import ResolvedInputRegistry
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import InverterTimestepPlan
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

from .battery import BatteryComponent
from .pv import PvComponent

_CURTAIL_POWER_THRESHOLD_KW = 0.01


class InverterRun:
    def __init__(self, *, inverter_connection: Connection) -> None:
        self.inverter_connection = inverter_connection


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

        self._latest: InverterRun | None = None

    def update_inputs(
        self,
        *,
        horizon: Horizon,
        inputs: ResolvedInputRegistry,
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
    ) -> list[GraphElement]:
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

        if self.pv is not None:
            elements.extend(self.pv.graph_elements(horizon=horizon))
        if self.battery is not None:
            elements.extend(
                self.battery.graph_elements(
                    horizon=horizon,
                    grid_connection=grid_connection,
                    price_import_raw=price_import_raw,
                )
            )

        self._latest = InverterRun(inverter_connection=inverter_connection)
        return elements

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[InverterTimestepPlan]:
        horizon = snapshot.ctx.horizon

        if self._latest is None:
            raise ValueError("InverterComponent has not been built for this run")

        inverter_connection = self._latest.inverter_connection

        batt_conn = None
        batt_node = None
        batt_capacity = None
        if self.battery is not None:
            batt_conn = self.battery.latest_connection()
            batt_node = self.battery.latest_storage()
            batt_capacity = self.battery.capacity_kwh

        for t in horizon.T:
            pv_kw = 0.0
            pv_curtail_kw = None
            curtailment_active = None
            if self.pv is not None:
                pv_kw = self.pv.pv_kw(snapshot, t)
                pv_curtail_kw = self.pv.curtail_kw(snapshot, t)
                curtailment_active = (
                    None
                    if pv_curtail_kw is None
                    else bool(float(pv_curtail_kw) > _CURTAIL_POWER_THRESHOLD_KW)
                )

            ac_into = value_of(inverter_connection.flow_into_node(self.ac_bus_id).get(t))
            ac_out = value_of(inverter_connection.flow_out_of_node(self.ac_bus_id).get(t))
            ac_net = ac_into - ac_out

            batt_charge_kw = None
            batt_discharge_kw = None
            batt_soc_kwh = None
            batt_soc_pct = None
            if batt_conn is not None and batt_node is not None:
                batt_charge_kw = value_of(batt_conn.flow_into_node(batt_node.id).get(t))
                batt_discharge_kw = value_of(batt_conn.flow_out_of_node(batt_node.id).get(t))
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
