from __future__ import annotations

from typing import Literal

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec, ObjectiveTerm
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphModel, EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    ExclusiveDirection,
    LinearCostSeries,
)
from energy_assistant.ems.topology.nodes import StorageNodeTemplate


class BatteryComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraphTemplate,
        inverter_id: str,
        dc_bus_id: str,
        capacity_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        reserve_kwh: float,
        storage_efficiency: float,
        initial_soc_kwh_key: str,
        terminal_mode: Literal["hard", "adaptive"],
        terminal_penalty_per_kwh: float | Literal["mean", "median"] | None,
        terminal_soc_value_per_kwh: float | None,
        price_import_raw_key: str = "price_import_raw",
        max_charge_kw: float,
        max_discharge_kw: float,
        charge_cost_key: str,
        discharge_cost_key: str,
        time_cost_key: str,
        grid_connection_id: str,
        grid_max_export_kw: float,
    ) -> None:
        self.inverter_id = str(inverter_id)
        self.dc_bus_id = str(dc_bus_id)
        self.capacity_kwh = float(capacity_kwh)
        self.soc_min_kwh = float(soc_min_kwh)
        self.soc_max_kwh = float(soc_max_kwh)
        self.reserve_kwh = float(reserve_kwh)
        self.storage_efficiency = float(storage_efficiency)

        self.node_id = f"{self.inverter_id}_battery"
        self.connection_id = f"battery_{self.inverter_id}_link"

        graph.add_storage(
            StorageNodeTemplate(
                id=self.node_id,
                name=f"Battery {self.inverter_id}",
                capacity_kwh=self.capacity_kwh,
                soc_min_kwh=self.soc_min_kwh,
                soc_max_kwh=self.soc_max_kwh,
                storage_efficiency=self.storage_efficiency,
                initial_soc_kwh_key=str(initial_soc_kwh_key),
                terminal_mode=terminal_mode,
                terminal_reserve_kwh=self.reserve_kwh,
                terminal_penalty_per_kwh=terminal_penalty_per_kwh,
                price_import_raw_key=str(price_import_raw_key),
                terminal_soc_value_per_kwh=terminal_soc_value_per_kwh,
                mode="bidirectional",
            )
        )

        graph.add_connection(
            ConnectionTemplate(
                id=self.connection_id,
                a_node_id=self.dc_bus_id,
                b_node_id=self.node_id,
                link_components=[
                    # a_to_b is charge, b_to_a is discharge
                    DirectionalLimit(
                        max_a_to_b_kw=float(max_charge_kw),
                        max_b_to_a_kw=float(max_discharge_kw),
                    ),
                    ExclusiveDirection(),
                    LinearCostSeries(
                        cost_a_to_b_key=str(charge_cost_key),
                        cost_b_to_a_key=str(discharge_cost_key),
                        name=f"batt_wear_{self.inverter_id}",
                    ),
                    LinearCostSeries(
                        cost_a_to_b_key=str(time_cost_key),
                        cost_b_to_a_key=str(time_cost_key),
                        name=f"batt_time_{self.inverter_id}",
                    ),
                ],
            )
        )

        graph.add_fragment(
            BatteryExportReservePolicyTemplate(
                battery_node_id=self.node_id,
                grid_connection_id=str(grid_connection_id),
                reserve_kwh=self.reserve_kwh,
                soc_min_kwh=self.soc_min_kwh,
                soc_max_kwh=self.soc_max_kwh,
                grid_max_export_kw=float(grid_max_export_kw),
            )
        )


class BatteryExportReservePolicyTemplate:
    def __init__(
        self,
        *,
        battery_node_id: str,
        grid_connection_id: str,
        reserve_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        grid_max_export_kw: float,
    ) -> None:
        self.battery_node_id = str(battery_node_id)
        self.grid_connection_id = str(grid_connection_id)
        self.reserve_kwh = float(reserve_kwh)
        self.soc_min_kwh = float(soc_min_kwh)
        self.soc_max_kwh = float(soc_max_kwh)
        self.grid_max_export_kw = float(grid_max_export_kw)

    def bind(self, graph: EnergyGraphModel) -> BatteryExportReservePolicyModel:
        return BatteryExportReservePolicyModel(graph=graph, template=self)


class BatteryExportReservePolicyModel:
    """Blocks *all* grid export unless the battery stays above reserve SoC (parity with legacy)."""

    def __init__(
        self,
        *,
        graph: EnergyGraphModel,
        template: BatteryExportReservePolicyTemplate,
    ) -> None:
        self.battery_node_id = template.battery_node_id
        self.grid_connection_id = template.grid_connection_id

        ctx = graph.ctx
        batt = graph.storage_nodes[self.battery_node_id]
        grid_conn = graph.connections[self.grid_connection_id]

        # Grid export is a_to_b on the grid connection (AC -> Grid).
        P_grid_export = grid_conn.P_a_to_b
        export_ok = pulp.LpVariable.dicts(
            f"Export_ok_{self.battery_node_id}",
            ctx.horizon.T,
            lowBound=0,
            upBound=1,
            cat="Binary",
        )
        self.export_ok = export_ok

        reserve_kwh = float(template.reserve_kwh)
        soc_m = float(template.soc_max_kwh) - float(template.soc_min_kwh)

        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"batt_export_reserve_start_{self.battery_node_id}_t{t}",
                    batt.E_by_i[t] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                )
            )
            self._constraints.append(
                ConstraintSpec(
                    f"batt_export_reserve_end_{self.battery_node_id}_t{t}",
                    batt.E_by_i[t + 1] >= reserve_kwh - soc_m * (1 - export_ok[t]),
                )
            )
            self._constraints.append(
                ConstraintSpec(
                    f"grid_export_reserve_{self.battery_node_id}_t{t}",
                    P_grid_export[t] <= float(template.grid_max_export_kw) * export_ok[t],
                )
            )

        self._objective_terms: list[ObjectiveTerm] = []

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)
