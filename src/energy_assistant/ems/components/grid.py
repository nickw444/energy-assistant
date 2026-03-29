from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import timedelta

from energy_assistant.ems.forecast_alignment import (
    PriceForecastAligner,
    validate_forecast_coverage,
)
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.milp.context import value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import GridTimestepPlan
from energy_assistant.ems.parameters import SeriesParameter
from energy_assistant.ems.pricing import PriceSeriesBuilder
from energy_assistant.ems.time_windows import TimeWindowMatcher
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import (
    DirectionalLimit,
    LinearCost,
    SoftDirectionalLimit,
)
from energy_assistant.lib.source_resolver.hass_source import (
    HomeAssistantCurrencyEntitySource,
    HomeAssistantHistoricalAveragePriceForecastSource,
)
from energy_assistant.lib.source_resolver.models import PriceForecastInterval
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.plant import GridConfig


class GridRun:
    def __init__(
        self,
        *,
        connection: Connection,
        price_import_raw: list[float],
        price_export_raw: list[float],
        price_import_effective: list[float],
        price_export_effective: list[float],
        import_allowed: list[bool],
    ) -> None:
        self.connection = connection
        self.price_import_raw = [float(v) for v in price_import_raw]
        self.price_export_raw = [float(v) for v in price_export_raw]
        self.price_import_effective = [float(v) for v in price_import_effective]
        self.price_export_effective = [float(v) for v in price_export_effective]
        self.import_allowed = [bool(v) for v in import_allowed]


class GridComponent:
    """Grid import/export interface on the AC bus."""

    def __init__(
        self,
        *,
        bus_id: str,
        grid: GridConfig,
        node_id: str = "grid",
        connection_id: str = "grid_link",
    ) -> None:
        self.bus_id = str(bus_id)
        self.node_id = str(node_id)
        self.connection_id = str(connection_id)
        self._grid_cfg = grid

        self._price_aligner = PriceForecastAligner()
        self._time_window_matcher = TimeWindowMatcher()
        self._price_series_builder = PriceSeriesBuilder(
            grid_price_bias_pct=float(grid.grid_price_bias_pct),
            grid_price_risk=grid.grid_price_risk,
        )

        self._price_import_raw = SeriesParameter[float]("grid_price_import_raw")
        self._price_export_raw = SeriesParameter[float]("grid_price_export_raw")
        self._price_import_effective = SeriesParameter[float]("grid_price_import_effective")
        self._price_export_effective = SeriesParameter[float]("grid_price_export_effective")
        self._import_allowed = SeriesParameter[bool]("grid_import_allowed")
        self._latest: GridRun | None = None

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        cfg = self._grid_cfg
        resolver.mark_for_hydration(cfg.realtime_price_import)
        resolver.mark_for_hydration(cfg.realtime_price_export)
        resolver.mark_for_hydration(cfg.price_import_forecast)
        resolver.mark_for_hydration(cfg.price_export_forecast)
        extension = cfg.price_forecast_extension
        if extension is not None:
            resolver.mark_for_hydration(
                self._build_price_extension_source(
                    realtime_source=cfg.realtime_price_import,
                    history_days=extension.history_days,
                    interval_duration=extension.interval_duration,
                    forecast_horizon_hours=48,
                )
            )
            resolver.mark_for_hydration(
                self._build_price_extension_source(
                    realtime_source=cfg.realtime_price_export,
                    history_days=extension.history_days,
                    interval_duration=extension.interval_duration,
                    forecast_horizon_hours=48,
                )
            )

    def validate_forecast_coverage(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        import_intervals = self._resolve_price_intervals(
            horizon=horizon,
            resolver=resolver,
            direction="import",
        )
        export_intervals = self._resolve_price_intervals(
            horizon=horizon,
            resolver=resolver,
            direction="export",
        )
        validate_forecast_coverage(
            label="Grid import price forecast",
            horizon=horizon,
            intervals=import_intervals,
            allow_first_slot_missing=True,
        )
        validate_forecast_coverage(
            label="Grid export price forecast",
            horizon=horizon,
            intervals=export_intervals,
            allow_first_slot_missing=True,
        )

    def update_inputs(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        cfg = self._grid_cfg
        realtime_import = float(resolver.resolve(cfg.realtime_price_import))
        realtime_export = float(resolver.resolve(cfg.realtime_price_export))
        import_intervals = self._resolve_price_intervals(
            horizon=horizon,
            resolver=resolver,
            direction="import",
        )
        export_intervals = self._resolve_price_intervals(
            horizon=horizon,
            resolver=resolver,
            direction="export",
        )

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

        price_series = self._price_series_builder.build_series(
            horizon=horizon,
            price_import=price_import,
            price_export=price_export,
        )
        import_eff = [float(x) for x in price_series.import_effective]
        export_eff = [float(x) for x in price_series.export_effective]

        import_allowed = self._resolve_import_allowed(horizon)

        self._price_import_raw.set(price_import_raw)
        self._price_export_raw.set(price_export_raw)
        self._price_import_effective.set(import_eff)
        self._price_export_effective.set(export_eff)
        self._import_allowed.set(import_allowed)

    def graph_elements(self, *, horizon: Horizon) -> list[GraphElement]:
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
            float(cfg.max_import_kw) * (1.0 if ok else 0.0) for ok in import_allowed
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

        # a_node is AC bus; b_node is grid. Convention:
        # - export is a_to_b (AC -> grid)
        # - import is b_to_a (grid -> AC)
        connection = Connection(
            horizon=horizon,
            id=self.connection_id,
            a_node_id=self.bus_id,
            b_node_id=self.node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=float(cfg.max_export_kw),
                    max_b_to_a_kw=float(cfg.max_import_kw),
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

        self._latest = GridRun(
            connection=connection,
            price_import_raw=self._price_import_raw.get(),
            price_export_raw=self._price_export_raw.get(),
            price_import_effective=import_eff,
            price_export_effective=export_eff,
            import_allowed=import_allowed,
        )
        return [node, connection]

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

    def latest_connection(self) -> Connection:
        if self._latest is None:
            raise ValueError("GridComponent has not been built for this run")
        return self._latest.connection

    def latest_price_import_raw(self) -> list[float]:
        return self._price_import_raw.get()

    def latest_price_export_raw(self) -> list[float]:
        return self._price_export_raw.get()

    def latest_price_import_effective(self) -> list[float]:
        return self._price_import_effective.get()

    def latest_price_export_effective(self) -> list[float]:
        return self._price_export_effective.get()

    def _resolve_price_intervals(
        self,
        *,
        horizon: Horizon,
        resolver: ValueResolver,
        direction: str,
    ) -> list[PriceForecastInterval]:
        cfg = self._grid_cfg
        if direction == "import":
            base_intervals = resolver.resolve(cfg.price_import_forecast)
            realtime_source = cfg.realtime_price_import
        else:
            base_intervals = resolver.resolve(cfg.price_export_forecast)
            realtime_source = cfg.realtime_price_export

        extension_cfg = cfg.price_forecast_extension
        if extension_cfg is None:
            return list(base_intervals)

        extension_source = self._build_price_extension_source(
            realtime_source=realtime_source,
            history_days=extension_cfg.history_days,
            interval_duration=extension_cfg.interval_duration,
            forecast_horizon_hours=self._forecast_extension_horizon_hours(
                horizon,
                interval_duration=extension_cfg.interval_duration,
            ),
        )
        extension_intervals = resolver.resolve(extension_source)
        return self._merge_price_forecast_extension(
            base_intervals=list(base_intervals),
            extension_intervals=list(extension_intervals),
        )

    def _build_price_extension_source(
        self,
        *,
        realtime_source: HomeAssistantCurrencyEntitySource,
        history_days: int,
        interval_duration: int,
        forecast_horizon_hours: int,
    ) -> HomeAssistantHistoricalAveragePriceForecastSource:
        return HomeAssistantHistoricalAveragePriceForecastSource(
            type="home_assistant",
            platform="historical_average_price",
            entity=realtime_source.entity,
            history_days=history_days,
            interval_duration=interval_duration,
            forecast_horizon_hours=forecast_horizon_hours,
        )

    def _forecast_extension_horizon_hours(
        self,
        horizon: Horizon,
        *,
        interval_duration: int,
    ) -> int:
        extension_start = floor_to_interval_boundary(horizon.now, interval_duration)
        required_duration = horizon.slots[-1].end - extension_start
        required_minutes = max(1.0, required_duration / timedelta(minutes=1))
        return max(1, math.ceil(required_minutes / 60.0))

    def _merge_price_forecast_extension(
        self,
        *,
        base_intervals: list[PriceForecastInterval],
        extension_intervals: list[PriceForecastInterval],
    ) -> list[PriceForecastInterval]:
        if not base_intervals:
            return extension_intervals
        if not extension_intervals:
            return base_intervals

        ordered_base = sorted(base_intervals, key=lambda interval: interval.start)
        ordered_extension = sorted(extension_intervals, key=lambda interval: interval.start)
        forecast_end = ordered_base[-1].end
        merged = list(ordered_base)
        for interval in ordered_extension:
            if interval.end <= forecast_end:
                continue
            if interval.start < forecast_end:
                merged.append(
                    PriceForecastInterval(
                        start=forecast_end,
                        end=interval.end,
                        value=float(interval.value),
                    )
                )
                continue
            merged.append(interval)
        return merged

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[GridTimestepPlan]:
        if self._latest is None:
            raise ValueError("GridComponent has not been built for this run")

        horizon = snapshot.ctx.horizon

        connection = self._latest.connection
        allowed = self._latest.import_allowed
        slack = connection.policy("import_soft_limit", SoftDirectionalLimit).slack_kw(connection)

        for t in horizon.T:
            export_kw = value_of(connection.flow_out_of_node(self.bus_id).get(t))
            import_kw = value_of(connection.flow_into_node(self.bus_id).get(t))
            yield GridTimestepPlan(
                import_kw=import_kw,
                export_kw=export_kw,
                net_kw=float(import_kw) - float(export_kw),
                import_allowed=bool(allowed[t]) if t < len(allowed) else None,
                import_violation_kw=value_of(slack.get(t)),
            )
