from __future__ import annotations

from dataclasses import dataclass

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import GridComponentPlan
from energy_assistant.ems.parameters import SeriesParameter
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.planning.pricing import PriceSeriesBuilder
from energy_assistant.ems.planning.time_windows import TimeWindowMatcher
from energy_assistant.ems.series import bool_series, interval_series_points
from energy_assistant.ems.system.component import EmsComponent
from energy_assistant.ems.system.topology import ComponentTopology, GraphBuildContext, PlanContext
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
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

    def __init__(
        self,
        *,
        bus_id: str,
        component_id: str,
        grid: GridComponentConfig,
        time_window_matcher: TimeWindowMatcher,
        price_series_builder: PriceSeriesBuilder,
    ) -> None:
        self.id = str(component_id)
        self.bus_id = str(bus_id)
        self.node_id = self.id
        self.connection_id = f"{self.id}_link"
        self._grid_cfg = grid
        self._connection_target_id = str(bus_id)

        self._time_window_matcher = time_window_matcher
        self._price_series_builder = price_series_builder

        self._price_import_raw = SeriesParameter[float](f"{self.id}_price_import_raw")
        self._price_export_raw = SeriesParameter[float](f"{self.id}_price_export_raw")
        self._price_import_effective = SeriesParameter[float](f"{self.id}_price_import_effective")
        self._price_export_effective = SeriesParameter[float](f"{self.id}_price_export_effective")
        self._import_allowed = SeriesParameter[bool](f"{self.id}_import_allowed")

    def describe_topology(self) -> ComponentTopology:
        return ComponentTopology(
            component_id=self.id,
            component_type="grid",
            connection_target_id=self._connection_target_id,
        )

    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        price_import_raw = inputs.forecast(
            self._grid_cfg.price_import.source.key,
            kind=InputValueKind.PRICE,
        )
        price_export_raw = inputs.forecast(
            self._grid_cfg.price_export.source.key,
            kind=InputValueKind.PRICE,
        )
        if len(price_import_raw) != horizon.num_intervals:
            raise ValueError("Grid import price series length does not match horizon")
        if len(price_export_raw) != horizon.num_intervals:
            raise ValueError("Grid export price series length does not match horizon")

        price_series = self._price_series_builder.build_series(
            horizon=horizon,
            price_import=price_import_raw,
            import_binding=self._grid_cfg.price_import,
            price_export=price_export_raw,
            export_binding=self._grid_cfg.price_export,
        )
        import_allowed = self._resolve_import_allowed(horizon)

        self._price_import_raw.set(price_import_raw)
        self._price_export_raw.set(price_export_raw)
        self._price_import_effective.set(price_series.import_effective)
        self._price_export_effective.set(price_series.export_effective)
        self._import_allowed.set(import_allowed)

    def build_graph(
        self,
        *,
        horizon: Horizon,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], GridSolveState]:
        _ = build_ctx
        return self.graph_elements(horizon=horizon)

    def graph_elements(self, *, horizon: Horizon) -> tuple[list[GraphElement], GridSolveState]:
        cfg = self._grid_cfg
        import_eff = self._price_import_effective.get()
        export_eff = self._price_export_effective.get()
        import_allowed = self._import_allowed.get()
        export_bonus = 1e-4 if cfg.zero_price_export_preference == "export" else -1e-4
        export_eff_with_bonus = [
            export_bonus if abs(float(export_eff[t])) <= 1e-9 else float(export_eff[t])
            for t in range(len(export_eff))
        ]
        grid_import_cost_per_kwh = import_eff
        grid_export_cost_per_kwh = [-float(x) for x in export_eff_with_bonus]
        w_early = 1e-4
        grid_early_cost_per_kwh = [(-w_early * (1.0 / (t + 1))) for t in horizon.T]
        import_limit_kw = [
            float(cfg.constraints.max_import_kw) * (1.0 if ok else 0.0) for ok in import_allowed
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
            id=self.connection_id,
            a_node_id=self.bus_id,
            b_node_id=self.node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=float(cfg.constraints.max_export_kw),
                    max_b_to_a_kw=float(cfg.constraints.max_import_kw),
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
            price_import_raw=self._price_import_raw.get(),
            price_export_raw=self._price_export_raw.get(),
            price_import_effective=import_eff,
            price_export_effective=export_eff,
            import_allowed=import_allowed,
        )
        return [node, connection], solve_state

    def _resolve_import_allowed(self, horizon: Horizon) -> list[bool]:
        forbidden = self._grid_cfg.import_forbidden_periods
        if not forbidden:
            return [True] * int(horizon.num_intervals)
        allowed: list[bool] = []
        for slot in horizon.slots:
            allowed.append(not self._time_window_matcher.matches(forbidden, slot.start))
        if len(allowed) != int(horizon.num_intervals):
            raise ValueError("import_allowed series length mismatch")
        return allowed

    def price_export_bias_pct(self) -> float:
        return self._price_series_builder.binding_bias_pct(
            binding=self._grid_cfg.price_export,
            direction="export",
        )

    @property
    def max_export_kw(self) -> float:
        return float(self._grid_cfg.constraints.max_export_kw)

    def build_plan(
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
            value_of(connection.flow_into_node(self.bus_id).get(t)) for t in horizon.T
        ]
        export_kw = [
            value_of(connection.flow_out_of_node(self.bus_id).get(t)) for t in horizon.T
        ]
        net_kw = [float(import_kw[t]) - float(export_kw[t]) for t in horizon.T]
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
