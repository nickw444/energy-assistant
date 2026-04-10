from __future__ import annotations

from dataclasses import dataclass

import pulp

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ConstraintSpec, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import BatteryComponentPlan
from energy_assistant.ems.parameters import ScalarParameter
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.series import interval_series_points, state_series_points
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import StorageNode
from energy_assistant.ems.topology.policies import (
    DirectionalEfficiency,
    DirectionalLimit,
    LinearCost,
)
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import BatteryComponentConfig


@dataclass(frozen=True, slots=True)
class BatterySolveState:
    storage: StorageNode
    connection: Connection


class BatteryComponent:
    def __init__(
        self,
        *,
        component_id: str,
        inverter_id: str,
        dc_bus_id: str,
        inverter_peak_kw: float,
        battery: BatteryComponentConfig,
        grid_max_export_kw: float,
    ) -> None:
        self.id = str(component_id)
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

        self.node_id = self.id
        self.connection_id = f"{self.id}_link"
        self.name = str(battery.name)

        self._battery_cfg = battery
        self._grid_max_export_kw = float(grid_max_export_kw)

        self._initial_soc_kwh = ScalarParameter[float](
            f"{self.id}_initial_soc_kwh"
        )

    def update_inputs(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
    ) -> None:
        _ = horizon
        initial_soc_pct = inputs.scalar_float(
            self._battery_cfg.state_of_charge_pct.key,
            kind=InputValueKind.PERCENTAGE,
        )
        initial_soc_kwh = self.capacity_kwh * initial_soc_pct / 100.0
        self._initial_soc_kwh.set(max(0.0, min(self.capacity_kwh, initial_soc_kwh)))

    def graph_elements(
        self,
        *,
        horizon: Horizon,
        grid_connection: Connection,
        price_import_raw: list[float],
    ) -> tuple[list[GraphElement], BatterySolveState]:
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
            name=self.name,
            capacity_kwh=self.capacity_kwh,
            soc_min_kwh=self.soc_min_kwh,
            soc_max_kwh=self.soc_max_kwh,
            initial_soc_kwh=initial_soc_kwh,
            terminal_mode=self._battery_cfg.terminal_soc.mode,
            terminal_reserve_kwh=self.reserve_kwh,
            terminal_penalty_per_kwh=self._battery_cfg.terminal_soc.penalty_per_kwh,
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

        solve_state = BatterySolveState(storage=storage, connection=connection)
        return [storage, connection, reserve_policy], solve_state

    def build_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: BatterySolveState,
    ) -> BatteryComponentPlan:
        horizon = snapshot.ctx.horizon
        storage = solve_state.storage
        connection = solve_state.connection
        charge_kw = [value_of(connection.flow_into_node(storage.id).get(t)) for t in horizon.T]
        discharge_kw = [value_of(connection.flow_out_of_node(storage.id).get(t)) for t in horizon.T]
        soc_kwh = [value_of(storage.E_by_i.get(t)) for t in range(horizon.num_intervals + 1)]
        soc_pct = [
            (float(value) / float(self.capacity_kwh)) * 100.0 if self.capacity_kwh else 0.0
            for value in soc_kwh
        ]
        return BatteryComponentPlan(
            charge_kw=interval_series_points(horizon, charge_kw),
            discharge_kw=interval_series_points(horizon, discharge_kw),
            soc_kwh=state_series_points(horizon, soc_kwh),
            soc_pct=state_series_points(horizon, soc_pct),
        )


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
