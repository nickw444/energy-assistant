from __future__ import annotations

from dataclasses import dataclass

import pulp

from energy_assistant.ems.components.component import EmsComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ConstraintSpec, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import BatteryComponentPlan
from energy_assistant.ems.series import interval_series_points, state_series_points
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph, GraphElement
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import StorageNode
from energy_assistant.ems.topology.policies import (
    ConnectionPolicy,
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


class BatteryComponent(EmsComponent[BatterySolveState, BatteryComponentPlan]):
    def __init__(
        self,
        *,
        component_id: str,
        inverter: InverterComponent,
        battery: BatteryComponentConfig,
        grid_max_export_kw: float,
    ) -> None:
        self.id = component_id
        self.inverter = inverter
        self._config = battery
        self._grid_max_export_kw = grid_max_export_kw

        self.name = self._config.name
        self.node_id = NodeId(component_id)


    def _initial_soc_kwh_from_inputs(
        self,
        *,
        inputs: AppliedInputRegistry,
    ) -> float:
        initial_soc_pct = inputs.scalar_float(
            self._config.state_of_charge_pct.key,
            kind=InputValueKind.PERCENTAGE,
        )
        capacity_kwh = self._config.capacity_kwh
        initial_soc_kwh = capacity_kwh * initial_soc_pct / 100.0
        return max(0.0, min(capacity_kwh, initial_soc_kwh))

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], BatterySolveState]:
        _ = build_ctx
        initial_soc_kwh = self._initial_soc_kwh_from_inputs(inputs=inputs)
        charge_cost_per_kwh = [
            self._config.charge_cost_per_kwh
        ] * int(horizon.num_intervals)
        discharge_cost_per_kwh = [
            self._config.discharge_cost_per_kwh
        ] * int(horizon.num_intervals)

        # Time-weighted throughput penalty series (tiny, to stabilize early/late tie-breaks).
        w_batt_time = 1e-6
        time_cost_per_kwh = [w_batt_time * (t + 1) for t in horizon.T]

        policies: dict[str, ConnectionPolicy] = {}
        # With no explicit battery rate limits, let the inverter/DC graph bound flow.
        if self._config.max_charge_kw is not None or self._config.max_discharge_kw is not None:
            policies["directional_limit"] = DirectionalLimit(
                max_a_to_b_kw=self._config.max_charge_kw,
                max_b_to_a_kw=self._config.max_discharge_kw,
                exclusive=(
                    self._config.max_charge_kw is not None
                    and self._config.max_discharge_kw is not None
                ),
            )

        capacity_kwh = self._config.capacity_kwh
        soc_min_kwh = capacity_kwh * self._config.min_soc_pct / 100.0
        soc_max_kwh = capacity_kwh * self._config.max_soc_pct / 100.0
        reserve_kwh = capacity_kwh * self._config.reserve_soc_pct / 100.0
        eta = self._config.storage_efficiency_pct / 100.0

        storage = StorageNode(
            horizon=horizon,
            id=self.node_id,
            name=self.name,
            capacity_kwh=capacity_kwh,
            soc_min_kwh=soc_min_kwh,
            soc_max_kwh=soc_max_kwh,
            initial_soc_kwh=initial_soc_kwh,
            terminal_mode=self._config.terminal_soc.mode,
            terminal_reserve_kwh=reserve_kwh,
            terminal_penalty_per_kwh=self._config.terminal_soc.penalty_per_kwh,
            price_import_raw=None,
            terminal_soc_value_per_kwh=self._config.soc_value_per_kwh,
        )

        connection = Connection(
            horizon=horizon,
            id=f"{self.id}_link",
            a_node_id=self.inverter.dc_bus_id,
            b_node_id=self.node_id,
            policies={
                **policies,
                # a_to_b is charge, b_to_a is discharge
                "wear_cost": LinearCost(
                    cost_a_to_b_per_kwh=charge_cost_per_kwh,
                    cost_b_to_a_per_kwh=discharge_cost_per_kwh,
                    name=f"batt_wear_{self.inverter.id}",
                ),
                "time_cost": LinearCost(
                    cost_a_to_b_per_kwh=time_cost_per_kwh,
                    cost_b_to_a_per_kwh=time_cost_per_kwh,
                    name=f"batt_time_{self.inverter.id}",
                ),
                "efficiency": DirectionalEfficiency(
                    eta_a_to_b=eta,
                    eta_b_to_a=eta,
                ),
            },
        )

        solve_state = BatterySolveState(storage=storage, connection=connection)
        return [storage, connection], solve_state

    def create_graph_fragments(
        self,
        *,
        graph: EnergyGraph,
        build_ctx: GraphBuildContext,
        solve_states: SolveStateStore,
    ) -> list[GraphElement]:
        _ = graph
        grids = build_ctx.components_of_type(GridComponent)
        same_switchboard_grids = [
            grid for grid in grids if grid.switchboard is self.inverter.switchboard
        ]
        grid_connections = [
            connection
            for grid in same_switchboard_grids
            for connection in build_ctx.connections(grid.id)
        ]
        battery_state = solve_states.get(self)

        grid_price_import_raw = [0.0] * int(battery_state.storage.horizon.num_intervals)
        if same_switchboard_grids:
            grid_solve_state = solve_states.get(same_switchboard_grids[0])
            grid_price_import_raw = list(grid_solve_state.price_import_raw)
        battery_state.storage.bind_terminal_import_prices(grid_price_import_raw)

        if not grid_connections:
            return []

        capacity_kwh = self._config.capacity_kwh
        reserve_kwh = capacity_kwh * self._config.reserve_soc_pct / 100.0
        soc_min_kwh = capacity_kwh * self._config.min_soc_pct / 100.0
        soc_max_kwh = capacity_kwh * self._config.max_soc_pct / 100.0

        return [
            BatteryExportReservePolicy(
                horizon=battery_state.storage.horizon,
                battery=battery_state.storage,
                grid_connections=grid_connections,
                reserve_kwh=reserve_kwh,
                soc_min_kwh=soc_min_kwh,
                soc_max_kwh=soc_max_kwh,
                grid_max_export_kw=self._grid_max_export_kw,
            )
        ]

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: BatterySolveState,
        plan_ctx: PlanContext,
    ) -> BatteryComponentPlan:
        _ = plan_ctx
        horizon = snapshot.ctx.horizon
        storage = solve_state.storage
        connection = solve_state.connection
        charge_kw = [value_of(connection.flow_into_node(storage.id).get(t)) for t in horizon.T]
        discharge_kw = [value_of(connection.flow_out_of_node(storage.id).get(t)) for t in horizon.T]
        soc_kwh = [value_of(storage.E_by_i.get(t)) for t in range(horizon.num_intervals + 1)]
        capacity_kwh = self._config.capacity_kwh
        soc_pct = [
            (value / capacity_kwh) * 100.0 if capacity_kwh else 0.0
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
        grid_connections: list[Connection],
        reserve_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        grid_max_export_kw: float,
    ) -> None:
        self._horizon = horizon
        self._battery = battery
        self._grids = list(grid_connections)
        self._reserve_kwh = reserve_kwh
        self._soc_min_kwh = soc_min_kwh
        self._soc_max_kwh = soc_max_kwh
        self._grid_max_export_kw = grid_max_export_kw
        self._export_ok_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}

    @property
    def constraints(self) -> list[ConstraintSpec]:
        batt = self._battery
        if not self._grids:
            return []

        reserve_kwh = self._reserve_kwh
        soc_m = self._soc_max_kwh - self._soc_min_kwh
        constraints: list[ConstraintSpec] = []

        for grid in self._grids:
            if grid.id not in self._export_ok_by_connection:
                self._export_ok_by_connection[grid.id] = pulp.LpVariable.dicts(
                    f"Export_ok_{batt.id}_{grid.id}",
                    self._horizon.T,
                    lowBound=0,
                    upBound=1,
                    cat="Binary",
                )
            export_ok = self._export_ok_by_connection[grid.id]
            # Grid export is a_to_b on the grid connection (AC -> Grid).
            P_grid_export = grid.flow_out_of_node(grid.a_node_id)

            for t in self._horizon.T:
                constraints.append(
                    ConstraintSpec(
                        f"batt_export_reserve_start_{batt.id}_{grid.id}_t{t}",
                        batt.E_by_i[t] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                    )
                )
                constraints.append(
                    ConstraintSpec(
                        f"batt_export_reserve_end_{batt.id}_{grid.id}_t{t}",
                        batt.E_by_i[t + 1] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                    )
                )
                constraints.append(
                    ConstraintSpec(
                        f"grid_export_reserve_{batt.id}_{grid.id}_t{t}",
                        P_grid_export[t] <= self._grid_max_export_kw * export_ok[t],
                    )
                )
        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.LpAffineExpression()
