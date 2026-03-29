from __future__ import annotations

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.parameters import ScalarParameter
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import StorageNode
from energy_assistant.ems.topology.policies import (
    DirectionalEfficiency,
    DirectionalLimit,
    LinearCost,
)
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import TerminalSocConfig
from energy_assistant.models.plant import BatteryConfig


class BatteryExportReservePolicy:
    """Blocks *all* grid export unless the battery stays above reserve SoC (parity with legacy)."""

    def __init__(
        self,
        *,
        horizon: Horizon,
        battery: StorageNode,
        grid_connection: Connection,
        reserve_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        grid_max_export_kw: float,
    ) -> None:
        self._horizon = horizon
        self._battery = battery
        self._grid = grid_connection
        self._reserve_kwh = float(reserve_kwh)
        self._soc_min_kwh = float(soc_min_kwh)
        self._soc_max_kwh = float(soc_max_kwh)
        self._grid_max_export_kw = float(grid_max_export_kw)
        self._export_ok_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}

    @property
    def constraints(self) -> list[ConstraintSpec]:
        batt = self._battery
        grid = self._grid

        if grid.id not in self._export_ok_by_connection:
            self._export_ok_by_connection[grid.id] = pulp.LpVariable.dicts(
                f"Export_ok_{batt.id}_{grid.id}",
                self._horizon.T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        export_ok = self._export_ok_by_connection[grid.id]
        reserve_kwh = float(self._reserve_kwh)
        soc_m = float(self._soc_max_kwh) - float(self._soc_min_kwh)

        # Grid export is a_to_b on the grid connection (AC -> Grid).
        P_grid_export = grid.flow_out_of_node(grid.a_node_id)

        constraints: list[ConstraintSpec] = []
        for t in self._horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"batt_export_reserve_start_{batt.id}_t{t}",
                    batt.E_by_i[t] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                )
            )
            constraints.append(
                ConstraintSpec(
                    f"batt_export_reserve_end_{batt.id}_t{t}",
                    batt.E_by_i[t + 1] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                )
            )
            constraints.append(
                ConstraintSpec(
                    f"grid_export_reserve_{batt.id}_t{t}",
                    P_grid_export[t] <= float(self._grid_max_export_kw) * export_ok[t],
                )
            )
        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.LpAffineExpression()


class BatteryRun:
    def __init__(self, *, storage: StorageNode, connection: Connection) -> None:
        self.storage = storage
        self.connection = connection


class BatteryComponent:
    def __init__(
        self,
        *,
        inverter_id: str,
        dc_bus_id: str,
        inverter_peak_kw: float,
        battery: BatteryConfig,
        grid_max_export_kw: float,
        terminal_soc: TerminalSocConfig,
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
        self._eta = float(battery.storage_efficiency_pct) / 100.0

        self.node_id = f"{self.inverter_id}_battery"
        self.connection_id = f"battery_{self.inverter_id}_link"

        self._battery_cfg = battery
        self._grid_max_export_kw = float(grid_max_export_kw)
        self._terminal_soc = terminal_soc

        self._initial_soc_kwh = ScalarParameter[float](
            f"{self.inverter_id}_battery_initial_soc_kwh"
        )
        self._latest: BatteryRun | None = None

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        resolver.mark_for_hydration(self._battery_cfg.state_of_charge_pct)
        resolver.mark_for_hydration(self._battery_cfg.realtime_power)

    def update_inputs(
        self,
        *,
        horizon: Horizon,
        resolver: ValueResolver,
    ) -> None:
        _ = horizon
        initial_soc_pct = float(resolver.resolve(self._battery_cfg.state_of_charge_pct))
        initial_soc_kwh = self.capacity_kwh * initial_soc_pct / 100.0
        self._initial_soc_kwh.set(max(0.0, min(self.capacity_kwh, initial_soc_kwh)))

    def graph_elements(
        self,
        *,
        horizon: Horizon,
        grid_connection: Connection,
        price_import_raw: list[float],
    ) -> list[GraphElement]:
        initial_soc_kwh = self._initial_soc_kwh.get()

        charge_cost_per_kwh = [
            float(self._battery_cfg.charge_cost_per_kwh)
        ] * int(horizon.num_intervals)
        discharge_cost_per_kwh = [
            float(self._battery_cfg.discharge_cost_per_kwh)
        ] * int(horizon.num_intervals)

        # Time-weighted throughput penalty series (tiny, to stabilize early/late tie-breaks).
        w_batt_time = 1e-6
        time_cost_per_kwh = [float(w_batt_time) * float(t + 1) for t in horizon.T]

        storage = StorageNode(
            horizon=horizon,
            id=self.node_id,
            name=f"Battery {self.inverter_id}",
            capacity_kwh=self.capacity_kwh,
            soc_min_kwh=self.soc_min_kwh,
            soc_max_kwh=self.soc_max_kwh,
            initial_soc_kwh=initial_soc_kwh,
            terminal_mode=self._terminal_soc.mode,
            terminal_reserve_kwh=self.reserve_kwh,
            terminal_penalty_per_kwh=self._terminal_soc.penalty_per_kwh,
            price_import_raw=price_import_raw,
            terminal_soc_value_per_kwh=self._battery_cfg.soc_value_per_kwh,
        )

        connection = Connection(
            horizon=horizon,
            id=self.connection_id,
            a_node_id=self.dc_bus_id,
            b_node_id=self.node_id,
            policies={
                # a_to_b is charge, b_to_a is discharge
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=float(self.max_charge_kw),
                    max_b_to_a_kw=float(self.max_discharge_kw),
                    exclusive=True,
                ),
                "wear_cost": LinearCost(
                    cost_a_to_b_per_kwh=charge_cost_per_kwh,
                    cost_b_to_a_per_kwh=discharge_cost_per_kwh,
                    name=f"batt_wear_{self.inverter_id}",
                ),
                "time_cost": LinearCost(
                    cost_a_to_b_per_kwh=time_cost_per_kwh,
                    cost_b_to_a_per_kwh=time_cost_per_kwh,
                    name=f"batt_time_{self.inverter_id}",
                ),
                "efficiency": DirectionalEfficiency(
                    eta_a_to_b=self._eta,
                    eta_b_to_a=self._eta,
                ),
            },
        )

        reserve_policy = BatteryExportReservePolicy(
            horizon=horizon,
            battery=storage,
            grid_connection=grid_connection,
            reserve_kwh=self.reserve_kwh,
            soc_min_kwh=self.soc_min_kwh,
            soc_max_kwh=self.soc_max_kwh,
            grid_max_export_kw=self._grid_max_export_kw,
        )

        self._latest = BatteryRun(storage=storage, connection=connection)
        return [storage, connection, reserve_policy]

    def latest_storage(self) -> StorageNode:
        if self._latest is None:
            raise ValueError("BatteryComponent has not been built for this run")
        return self._latest.storage

    def latest_connection(self) -> Connection:
        if self._latest is None:
            raise ValueError("BatteryComponent has not been built for this run")
        return self._latest.connection
