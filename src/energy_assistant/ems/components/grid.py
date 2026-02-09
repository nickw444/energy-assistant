from __future__ import annotations

from collections.abc import Iterator

import pulp

from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.models import GridTimestepPlan
from energy_assistant.ems.system.system import ModelSnapshot
from energy_assistant.ems.topology.connection import ConnectionTemplate
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    ExclusiveDirection,
    LinearCostSeries,
    SoftDirectionalLimitSeries,
    SoftDirectionalLimitSeriesModel,
)
from energy_assistant.ems.topology.nodes import PortNodeTemplate


class GridComponent:
    """Bidirectional grid connection with pricing and optional forbidden-import slack."""

    def __init__(
        self,
        *,
        graph: EnergyGraphTemplate,
        switchboard_bus_id: str,
        grid_node_id: str = "grid",
        connection_id: str = "grid_link",
        max_import_kw: float,
        max_export_kw: float,
        import_allowed_bool_key: str = "grid_import_allowed",
        import_limit_kw_key: str = "grid_import_limit_kw",
        import_cost_key: str = "grid_import_cost_per_kwh",
        export_cost_key: str = "grid_export_cost_per_kwh",
        early_cost_key: str = "grid_early_cost_per_kwh",
        violation_penalty_per_kwh: float = 1e3,
    ) -> None:
        self.switchboard_bus_id = str(switchboard_bus_id)
        self.grid_node_id = str(grid_node_id)
        self.connection_id = str(connection_id)

        self._import_allowed_bool_key = str(import_allowed_bool_key)
        self._soft_limit_name = "grid_import_forbidden"

        graph.add_port(PortNodeTemplate(id=self.grid_node_id, name="Grid"))
        graph.add_connection(
            ConnectionTemplate(
                id=self.connection_id,
                a_node_id=self.switchboard_bus_id,
                b_node_id=self.grid_node_id,
                link_components=[
                    # a_to_b is export (AC -> Grid), b_to_a is import (Grid -> AC)
                    DirectionalLimit(
                        max_a_to_b_kw=float(max_export_kw),
                        max_b_to_a_kw=float(max_import_kw),
                    ),
                    ExclusiveDirection(),
                    SoftDirectionalLimitSeries(
                        direction="b_to_a",
                        limit_key=str(import_limit_kw_key),
                        penalty_per_kwh=float(violation_penalty_per_kwh),
                        name=self._soft_limit_name,
                    ),
                    LinearCostSeries(
                        cost_a_to_b_key=str(export_cost_key),
                        cost_b_to_a_key=str(import_cost_key),
                        name="grid_energy",
                    ),
                    LinearCostSeries(
                        cost_a_to_b_key=str(early_cost_key),
                        cost_b_to_a_key=str(early_cost_key),
                        name="grid_early",
                    ),
                ],
            )
        )

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[GridTimestepPlan]:
        conn = snapshot.graph.connections[self.connection_id]
        allowed = snapshot.ctx.inputs.bool_series(self._import_allowed_bool_key)

        # Find the soft-limit component to expose the slack series.
        slack_by_t: dict[int, pulp.LpVariable] | None = None
        for comp in conn.components:
            if (
                isinstance(comp, SoftDirectionalLimitSeriesModel)
                and comp.name == self._soft_limit_name
            ):
                slack_by_t = comp.slack_kw
                break

        for t in snapshot.ctx.horizon.T:
            import_kw = value_of(conn.P_b_to_a.get(t))
            export_kw = value_of(conn.P_a_to_b.get(t))
            import_violation_kw = None
            if slack_by_t is not None:
                import_violation_kw = value_of(slack_by_t.get(t))
            yield GridTimestepPlan(
                import_kw=import_kw,
                export_kw=export_kw,
                net_kw=import_kw - export_kw,
                import_allowed=bool(allowed[t]) if t < len(allowed) else None,
                import_violation_kw=import_violation_kw,
            )
