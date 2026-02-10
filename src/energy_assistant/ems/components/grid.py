from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from energy_assistant.ems.forecast_alignment import PriceForecastAligner, forecast_coverage_slots
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import GridTimestepPlan
from energy_assistant.ems.pricing import PriceSeriesBuilder
from energy_assistant.ems.time_windows import TimeWindowMatcher
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.link_components import (
    DirectionalLimit,
    LinearCost,
    SoftDirectionalLimit,
)
from energy_assistant.ems.topology.nodes import PortNode
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.plant import GridConfig


class GridComponent:
    """Grid import/export interface on the AC bus."""

    def __init__(
        self,
        *,
        graph: EnergyGraph,
        bus_id: str,
        grid: GridConfig,
        node_id: str = "grid",
        connection_id: str = "grid_link",
    ) -> None:
        self.bus_id = str(bus_id)
        self.node_id = str(node_id)
        self.connection_id = str(connection_id)
        self._grid_cfg = grid

        self.price_import_raw = DeferredSeries[float](name="price_import_raw")
        self.price_export_raw = DeferredSeries[float](name="price_export_raw")
        self.price_import_effective = DeferredSeries[float](name="price_import_effective")
        self.price_export_effective = DeferredSeries[float](name="price_export_effective")

        self.import_allowed = DeferredSeries[bool](name="grid_import_allowed")
        self.import_limit_kw = DeferredSeries[float](name="grid_import_limit_kw")

        self._grid_import_cost_per_kwh = DeferredSeries[float](name="grid_import_cost_per_kwh")
        self._grid_export_cost_per_kwh = DeferredSeries[float](name="grid_export_cost_per_kwh")
        self._grid_early_cost_per_kwh = DeferredSeries[float](name="grid_early_cost_per_kwh")

        self._price_aligner = PriceForecastAligner()
        self._time_window_matcher = TimeWindowMatcher()
        self._price_series_builder = PriceSeriesBuilder(
            grid_price_bias_pct=float(grid.grid_price_bias_pct),
            grid_price_risk=grid.grid_price_risk,
        )

        self.connection: Connection
        self._import_soft_limit: SoftDirectionalLimit

        graph.add_port(PortNode(id=self.node_id, name="Grid"))

        self._import_soft_limit = SoftDirectionalLimit(
            direction="b_to_a",
            limit_kw=self.import_limit_kw,
            penalty_per_kwh=1e3,
            name="grid_import_allowed",
        )

        # a_node is AC bus; b_node is grid. Convention:
        # - export is a_to_b (AC -> grid)
        # - import is b_to_a (grid -> AC)
        self.connection = Connection(
            id=self.connection_id,
            a_node_id=self.bus_id,
            b_node_id=self.node_id,
            link_components=[
                DirectionalLimit(
                    max_a_to_b_kw=float(grid.max_export_kw),
                    max_b_to_a_kw=float(grid.max_import_kw),
                    exclusive=True,
                ),
                self._import_soft_limit,
                LinearCost(
                    cost_a_to_b_per_kwh=self._grid_export_cost_per_kwh,
                    cost_b_to_a_per_kwh=self._grid_import_cost_per_kwh,
                    name="grid_energy",
                ),
                LinearCost(
                    cost_a_to_b_per_kwh=self._grid_early_cost_per_kwh,
                    cost_b_to_a_per_kwh=self._grid_early_cost_per_kwh,
                    name="grid_early",
                ),
            ],
        )
        graph.add_connection(self.connection)

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        cfg = self._grid_cfg
        resolver.mark_for_hydration(cfg.realtime_price_import)
        resolver.mark_for_hydration(cfg.realtime_price_export)
        resolver.mark_for_hydration(cfg.price_import_forecast)
        resolver.mark_for_hydration(cfg.price_export_forecast)

    def forecast_coverage_intervals(
        self, *, now: datetime, interval_minutes: int, resolver: ValueResolver
    ) -> int:
        start = floor_to_interval_boundary(now, interval_minutes)
        cfg = self._grid_cfg
        import_intervals = resolver.resolve(cfg.price_import_forecast)
        export_intervals = resolver.resolve(cfg.price_export_forecast)
        cov_import = forecast_coverage_slots(
            start,
            interval_minutes,
            import_intervals,
            allow_first_slot_missing=True,
        )
        cov_export = forecast_coverage_slots(
            start,
            interval_minutes,
            export_intervals,
            allow_first_slot_missing=True,
        )
        return int(min(cov_import, cov_export))

    def update(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        cfg = self._grid_cfg
        realtime_import = float(resolver.resolve(cfg.realtime_price_import))
        realtime_export = float(resolver.resolve(cfg.realtime_price_export))
        import_intervals = resolver.resolve(cfg.price_import_forecast)
        export_intervals = resolver.resolve(cfg.price_export_forecast)

        price_import = self._price_aligner.align(
            horizon,
            import_intervals,
            first_slot_override=realtime_import,
        )
        price_export = self._price_aligner.align(
            horizon,
            export_intervals,
            first_slot_override=realtime_export,
        )
        price_import_raw = [float(x) for x in price_import]
        price_export_raw = [float(x) for x in price_export]
        self.price_import_raw.set(price_import_raw)
        self.price_export_raw.set(price_export_raw)

        price_series = self._price_series_builder.build_series(
            horizon=horizon,
            price_import=price_import,
            price_export=price_export,
        )
        import_eff = [float(x) for x in price_series.import_effective]
        export_eff = [float(x) for x in price_series.export_effective]
        self.price_import_effective.set(import_eff)
        self.price_export_effective.set(export_eff)

        # Export tie-break bonus when effective export price is exactly zero.
        export_bonus = 1e-4 if cfg.zero_price_export_preference == "export" else -1e-4
        export_eff_with_bonus = [
            export_bonus if abs(float(export_eff[t])) <= 1e-9 else float(export_eff[t])
            for t in range(len(export_eff))
        ]

        # Grid flow costs: import is +price; export is -revenue.
        self._grid_import_cost_per_kwh.set(import_eff)
        self._grid_export_cost_per_kwh.set([-float(x) for x in export_eff_with_bonus])

        # Early-flow tie-break (tiny negative cost on any grid flow, stronger earlier).
        w_early = 1e-4
        self._grid_early_cost_per_kwh.set([(-w_early * (1.0 / (t + 1))) for t in horizon.T])

        import_allowed = self._resolve_import_allowed(horizon)
        self.import_allowed.set(import_allowed)
        self.import_limit_kw.set(
            [float(cfg.max_import_kw) * (1.0 if ok else 0.0) for ok in import_allowed]
        )

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

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[GridTimestepPlan]:
        horizon = snapshot.ctx.horizon
        allowed = self.import_allowed.get_for_horizon(horizon)

        slack = self._import_soft_limit.slack_kw(self.connection)

        for t in horizon.T:
            export_kw = value_of(self.connection.P_a_to_b.get(t))
            import_kw = value_of(self.connection.P_b_to_a.get(t))
            yield GridTimestepPlan(
                import_kw=import_kw,
                export_kw=export_kw,
                net_kw=float(import_kw) - float(export_kw),
                import_allowed=bool(allowed[t]) if t < len(allowed) else None,
                import_violation_kw=value_of(slack.get(t)),
            )
