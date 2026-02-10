from __future__ import annotations

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import Deferred, DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph, GraphFragment
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    LinearCost,
    StorageEfficiency,
)
from energy_assistant.ems.topology.nodes import StorageNode
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import TerminalSocConfig
from energy_assistant.models.plant import BatteryConfig


class BatteryExportReservePolicy(GraphFragment):
    """Blocks *all* grid export unless the battery stays above reserve SoC (parity with legacy)."""

    def __init__(
        self,
        *,
        battery: StorageNode,
        grid_connection: Connection,
        reserve_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        grid_max_export_kw: float,
    ) -> None:
        self._battery = battery
        self._grid = grid_connection
        self._reserve_kwh = float(reserve_kwh)
        self._soc_min_kwh = float(soc_min_kwh)
        self._soc_max_kwh = float(soc_max_kwh)
        self._grid_max_export_kw = float(grid_max_export_kw)
        self._constraints: list[ConstraintDescriptor] = []

    def set_horizon(self, horizon: Horizon, graph: EnergyGraph) -> None:
        _ = graph
        batt = self._battery
        grid = self._grid

        export_ok = grid.binary_series(f"Export_ok_{batt.id}")
        reserve_kwh = float(self._reserve_kwh)
        soc_m = float(self._soc_max_kwh) - float(self._soc_min_kwh)

        # Grid export is a_to_b on the grid connection (AC -> Grid).
        P_grid_export = grid.P_a_to_b

        constraints: list[ConstraintDescriptor] = []
        for t in horizon.T:
            constraints.append(
                ConstraintDescriptor(
                    f"batt_export_reserve_start_{batt.id}_t{t}",
                    batt.E_by_i[t] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                )
            )
            constraints.append(
                ConstraintDescriptor(
                    f"batt_export_reserve_end_{batt.id}_t{t}",
                    batt.E_by_i[t + 1] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                )
            )
            constraints.append(
                ConstraintDescriptor(
                    f"grid_export_reserve_{batt.id}_t{t}",
                    P_grid_export[t] <= float(self._grid_max_export_kw) * export_ok[t],
                )
            )
        self._constraints = constraints

    @property
    def constraints(self) -> list[ConstraintDescriptor]:
        return list(self._constraints)

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.LpAffineExpression()


class BatteryComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraph,
        inverter_id: str,
        dc_bus_id: str,
        inverter_peak_kw: float,
        battery: BatteryConfig,
        grid_connection: Connection,
        grid_max_export_kw: float,
        terminal_soc: TerminalSocConfig,
        price_import_raw: DeferredSeries[float],
    ) -> None:
        self.inverter_id = str(inverter_id)
        self.dc_bus_id = str(dc_bus_id)
        self.capacity_kwh = float(battery.capacity_kwh)

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

        self.max_charge_kw = float(charge_limit)
        self.max_discharge_kw = float(discharge_limit)

        self.soc_min_kwh = self.capacity_kwh * float(battery.min_soc_pct) / 100.0
        self.soc_max_kwh = self.capacity_kwh * float(battery.max_soc_pct) / 100.0
        self.reserve_kwh = self.capacity_kwh * float(battery.reserve_soc_pct) / 100.0
        eta = float(battery.storage_efficiency_pct) / 100.0

        self.node_id = f"{self.inverter_id}_battery"
        self.connection_id = f"battery_{self.inverter_id}_link"

        self._initial_soc_kwh = Deferred[float](name=f"battery_initial_soc_kwh:{self.inverter_id}")

        self._charge_cost_per_kwh = DeferredSeries[float](
            name=f"battery_charge_cost_per_kwh:{self.inverter_id}"
        )
        self._discharge_cost_per_kwh = DeferredSeries[float](
            name=f"battery_discharge_cost_per_kwh:{self.inverter_id}"
        )
        self._time_cost_per_kwh = DeferredSeries[float](
            name=f"battery_time_cost_per_kwh:{self.inverter_id}"
        )

        # Storage node uses connection-provided efficiency; the conversion itself is modeled as
        # a LinkComponent (StorageEfficiency).
        self.storage = StorageNode(
            id=self.node_id,
            name=f"Battery {self.inverter_id}",
            capacity_kwh=self.capacity_kwh,
            soc_min_kwh=self.soc_min_kwh,
            soc_max_kwh=self.soc_max_kwh,
            initial_soc_kwh=self._initial_soc_kwh,
            terminal_mode=terminal_soc.mode,
            terminal_reserve_kwh=self.reserve_kwh,
            terminal_penalty_per_kwh=terminal_soc.penalty_per_kwh,
            price_import_raw=price_import_raw,
            terminal_soc_value_per_kwh=battery.soc_value_per_kwh,
        )
        graph.add_storage(self.storage)

        self.connection = Connection(
            id=self.connection_id,
            a_node_id=self.dc_bus_id,
            b_node_id=self.node_id,
            link_components=[
                # a_to_b is charge, b_to_a is discharge
                DirectionalLimit(
                    max_a_to_b_kw=float(self.max_charge_kw),
                    max_b_to_a_kw=float(self.max_discharge_kw),
                    exclusive=True,
                ),
                StorageEfficiency(eta_a_to_b=eta, eta_b_to_a=eta),
                LinearCost(
                    cost_a_to_b_per_kwh=self._charge_cost_per_kwh,
                    cost_b_to_a_per_kwh=self._discharge_cost_per_kwh,
                    name=f"batt_wear_{self.inverter_id}",
                ),
                LinearCost(
                    cost_a_to_b_per_kwh=self._time_cost_per_kwh,
                    cost_b_to_a_per_kwh=self._time_cost_per_kwh,
                    name=f"batt_time_{self.inverter_id}",
                ),
            ],
        )
        graph.add_connection(self.connection)

        graph.add_fragment(
            BatteryExportReservePolicy(
                battery=self.storage,
                grid_connection=grid_connection,
                reserve_kwh=self.reserve_kwh,
                soc_min_kwh=self.soc_min_kwh,
                soc_max_kwh=self.soc_max_kwh,
                grid_max_export_kw=float(grid_max_export_kw),
            )
        )

        self._battery_cfg = battery

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        resolver.mark_for_hydration(self._battery_cfg.state_of_charge_pct)
        resolver.mark_for_hydration(self._battery_cfg.realtime_power)

    def update(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        initial_soc_pct = float(resolver.resolve(self._battery_cfg.state_of_charge_pct))
        initial_soc_kwh = self.capacity_kwh * initial_soc_pct / 100.0
        initial_soc_kwh = max(0.0, min(self.capacity_kwh, initial_soc_kwh))
        self._initial_soc_kwh.set(float(initial_soc_kwh))

        self._charge_cost_per_kwh.set(
            [float(self._battery_cfg.charge_cost_per_kwh)] * int(horizon.num_intervals)
        )
        self._discharge_cost_per_kwh.set(
            [float(self._battery_cfg.discharge_cost_per_kwh)] * int(horizon.num_intervals)
        )

        # Time-weighted throughput penalty series (tiny, to stabilize early/late tie-breaks).
        w_batt_time = 1e-6
        self._time_cost_per_kwh.set([float(w_batt_time) * float(t + 1) for t in horizon.T])
