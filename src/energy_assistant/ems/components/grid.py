from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import GridComponentPlan
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.planning.pricing import PriceSeriesBuilder
from energy_assistant.ems.planning.time_windows import TimeWindowMatcher
from energy_assistant.ems.series import bool_series, interval_series_points
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.context import GraphBuildContext, PlanContext
from energy_assistant.ems.system.types import ComponentType
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    DirectionalLimit,
    LinearCost,
    SoftDirectionalLimit,
)
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import GridComponentConfig


@dataclass(frozen=True, slots=True)
class GridSolveState:
    connection: Connection
    price_import_raw: list[float]
    price_export_raw: list[float]
    price_import_effective: list[float]
    price_export_effective: list[float]
    import_allowed: list[bool]


class GridComponent(EmsComponent[GridSolveState, GridComponentPlan]):
    """Grid import/export interface on the AC bus."""

    component_type: ClassVar[ComponentType] = "grid"

    def __init__(
        self,
        *,
        component_id: str,
        switchboard: SwitchboardComponent,
        grid: GridComponentConfig,
        time_window_matcher: TimeWindowMatcher,
        price_series_builder: PriceSeriesBuilder,
    ) -> None:
        self.id = component_id
        self.node_id = NodeId(component_id)

        self.switchboard = switchboard
        self._config = grid
        self._time_window_matcher = time_window_matcher
        self._price_series_builder = price_series_builder

    def _raw_price_series_from_inputs(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        source_key: str,
        label: str,
    ) -> list[float]:
        series = inputs.forecast(source_key, kind=InputValueKind.PRICE)
        if len(series) != horizon.num_intervals:
            raise ValueError(f"{label} series length does not match horizon")
        return list(series)

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], GridSolveState]:
        _ = build_ctx
        price_import_raw = self._raw_price_series_from_inputs(
            horizon=horizon,
            inputs=inputs,
            source_key=self._config.price_import.source.key,
            label="Grid import price",
        )
        price_export_raw = self._raw_price_series_from_inputs(
            horizon=horizon,
            inputs=inputs,
            source_key=self._config.price_export.source.key,
            label="Grid export price",
        )
        price_series = self._price_series_builder.build_series(
            horizon=horizon,
            price_import=price_import_raw,
            import_binding=self._config.price_import,
            price_export=price_export_raw,
            export_binding=self._config.price_export,
        )
        import_allowed = self._resolve_import_allowed(horizon)
        cfg = self._config
        import_eff = price_series.import_effective
        export_eff = price_series.export_effective
        export_bonus = 1e-4 if cfg.zero_price_export_preference == "export" else -1e-4
        export_eff_with_bonus = [
            export_bonus if abs(export_eff[t]) <= 1e-9 else export_eff[t]
            for t in range(len(export_eff))
        ]
        grid_import_cost_per_kwh = import_eff
        grid_export_cost_per_kwh = [-x for x in export_eff_with_bonus]
        w_early = 1e-4
        grid_early_cost_per_kwh = [(-w_early * (1.0 / (t + 1))) for t in horizon.T]
        import_limit_kw = [
            cfg.constraints.max_import_kw * (1.0 if ok else 0.0) for ok in import_allowed
        ]

        node = Node(
            horizon=horizon,
            id=self.node_id,
            name="Grid",
            node_role="prosumer",
        )

        import_soft_limit = SoftDirectionalLimit(
            direction="b_to_a",
            limit_kw=import_limit_kw,
            penalty_per_kwh=1e3,
            name="grid_import_allowed",
        )

        connection = Connection(
            horizon=horizon,
            id=f"{self.id}_link",
            a_node_id=self.switchboard.bus_id,
            b_node_id=self.node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=cfg.constraints.max_export_kw,
                    max_b_to_a_kw=cfg.constraints.max_import_kw,
                    exclusive=True,
                ),
                "import_soft_limit": import_soft_limit,
                "grid_energy_cost": LinearCost(
                    cost_a_to_b_per_kwh=grid_export_cost_per_kwh,
                    cost_b_to_a_per_kwh=grid_import_cost_per_kwh,
                    name="grid_energy",
                ),
                "grid_early_cost": LinearCost(
                    cost_a_to_b_per_kwh=grid_early_cost_per_kwh,
                    cost_b_to_a_per_kwh=grid_early_cost_per_kwh,
                    name="grid_early",
                ),
            },
        )

        solve_state = GridSolveState(
            connection=connection,
            price_import_raw=price_import_raw,
            price_export_raw=price_export_raw,
            price_import_effective=import_eff,
            price_export_effective=export_eff,
            import_allowed=import_allowed,
        )
        return [node, connection], solve_state

    def _resolve_import_allowed(self, horizon: Horizon) -> list[bool]:
        forbidden = self._config.import_forbidden_periods
        if not forbidden:
            return [True] * int(horizon.num_intervals)
        allowed: list[bool] = []
        for slot in horizon.slots:
            allowed.append(not self._time_window_matcher.matches(forbidden, slot.start))
        return allowed

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: GridSolveState,
        plan_ctx: PlanContext,
    ) -> GridComponentPlan:
        _ = plan_ctx
        horizon = snapshot.ctx.horizon
        connection = solve_state.connection
        import_kw = [
            value_of(connection.flow_into_node(self.switchboard.bus_id).get(t))
            for t in horizon.T
        ]
        export_kw = [
            value_of(connection.flow_out_of_node(self.switchboard.bus_id).get(t))
            for t in horizon.T
        ]
        net_kw = [import_kw[t] - export_kw[t] for t in horizon.T]
        return GridComponentPlan(
            price_import_raw=interval_series_points(horizon, solve_state.price_import_raw),
            price_export_raw=interval_series_points(horizon, solve_state.price_export_raw),
            price_import_effective=interval_series_points(
                horizon, solve_state.price_import_effective
            ),
            price_export_effective=interval_series_points(
                horizon, solve_state.price_export_effective
            ),
            import_allowed=interval_series_points(horizon, bool_series(solve_state.import_allowed)),
            import_kw=interval_series_points(horizon, import_kw),
            export_kw=interval_series_points(horizon, export_kw),
            net_kw=interval_series_points(horizon, net_kw),
        )
