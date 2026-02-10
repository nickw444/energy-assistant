from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import InverterTimestepPlan
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.link_components import DirectionalLimit
from energy_assistant.ems.topology.nodes import BusNode
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import TerminalSocConfig
from energy_assistant.models.plant import GridConfig, InverterConfig

from .battery import BatteryComponent
from .pv import PvComponent

_CURTAIL_POWER_THRESHOLD_KW = 0.01


class InverterComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraph,
        switchboard_bus_id: str,
        inverter: InverterConfig,
        grid_connection: Connection,
        grid_cfg: GridConfig,
        terminal_soc: TerminalSocConfig,
        price_import_raw: DeferredSeries[float],
    ) -> None:
        self.id = str(inverter.id)
        self.name = str(inverter.name)
        self.peak_power_kw = float(inverter.peak_power_kw)
        self.curtailment = inverter.curtailment

        self.ac_bus_id = str(switchboard_bus_id)
        self.dc_bus_id = f"{self.id}_dc"
        self.inverter_link_id = f"inverter_{self.id}_acdc"

        graph.add_bus(BusNode(id=self.dc_bus_id, name=f"DC Bus {self.id}", domain="dc"))

        # a_to_b is DC -> AC (inverting), b_to_a is AC -> DC (rectifying)
        self.inverter_connection = Connection(
            id=self.inverter_link_id,
            a_node_id=self.dc_bus_id,
            b_node_id=self.ac_bus_id,
            link_components=[
                DirectionalLimit(
                    max_a_to_b_kw=self.peak_power_kw,
                    max_b_to_a_kw=self.peak_power_kw,
                    exclusive=True,
                ),
            ],
        )
        graph.add_connection(self.inverter_connection)

        self.pv = PvComponent(graph=graph, inverter=inverter, dc_bus_id=self.dc_bus_id)

        self.battery: BatteryComponent | None = None
        if inverter.battery is not None:
            self.battery = BatteryComponent(
                graph=graph,
                inverter_id=self.id,
                dc_bus_id=self.dc_bus_id,
                inverter_peak_kw=self.peak_power_kw,
                battery=inverter.battery,
                grid_connection=grid_connection,
                grid_max_export_kw=float(grid_cfg.max_export_kw),
                terminal_soc=terminal_soc,
                price_import_raw=price_import_raw,
            )

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        self.pv.mark_for_hydration(resolver)
        if self.battery is not None:
            self.battery.mark_for_hydration(resolver)

    def forecast_coverage_intervals(
        self, *, now: datetime, interval_minutes: int, resolver: ValueResolver
    ) -> int:
        # Inverters only contribute PV forecast coverage (battery + inverter are realtime-only).
        return int(
            self.pv.forecast_coverage_intervals(
                now=now, interval_minutes=interval_minutes, resolver=resolver
            )
        )

    def update(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        self.pv.update(horizon=horizon, resolver=resolver)
        if self.battery is not None:
            self.battery.update(horizon=horizon, resolver=resolver)

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[InverterTimestepPlan]:
        horizon = snapshot.ctx.horizon

        batt_conn = None
        batt_node = None
        batt_capacity = None
        if self.battery is not None:
            batt_conn = self.battery.connection
            batt_node = self.battery.storage
            batt_capacity = self.battery.capacity_kwh

        for t in horizon.T:
            pv_kw = self.pv.pv_kw(snapshot, t)
            pv_curtail_kw = self.pv.curtail_kw(snapshot, t)
            curtailment_active = (
                None
                if pv_curtail_kw is None
                else bool(float(pv_curtail_kw) > _CURTAIL_POWER_THRESHOLD_KW)
            )

            ac_net = value_of(self.inverter_connection.P_a_to_b.get(t)) - value_of(
                self.inverter_connection.P_b_to_a.get(t)
            )

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
