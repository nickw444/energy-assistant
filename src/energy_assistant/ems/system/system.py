from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import (
    EconomicsTimestepPlan,
    EvTimestepPlan,
    GridTimestepPlan,
    InverterTimestepPlan,
    LoadsTimestepPlan,
    TimestepPlan,
)
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.lib.source_resolver.resolver import ValueResolver


class EmsSystem:
    """Persistent EMS system composed of Layer 1 components + a hidden Layer 0 topology."""

    def __init__(
        self,
        *,
        graph: EnergyGraph,
        switchboard_bus_id: str,
        base_load: BaseLoadComponent,
        grid: GridComponent,
        inverters: dict[str, InverterComponent],
        evs: dict[str, EvComponent],
    ) -> None:
        self.graph = graph
        self.switchboard_bus_id = str(switchboard_bus_id)
        self.base_load = base_load
        self.grid = grid
        self.inverters = dict(inverters)
        self.evs = dict(evs)

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        self.base_load.mark_for_hydration(resolver)
        self.grid.mark_for_hydration(resolver)
        for inv in self.inverters.values():
            inv.mark_for_hydration(resolver)
        for ev in self.evs.values():
            ev.mark_for_hydration(resolver)

    def forecast_coverage_intervals(
        self, *, now: datetime, interval_minutes: int, resolver: ValueResolver
    ) -> int:
        coverages: list[int] = []
        coverages.append(
            int(
                self.base_load.forecast_coverage_intervals(
                    now=now, interval_minutes=interval_minutes, resolver=resolver
                )
            )
        )
        coverages.append(
            int(
                self.grid.forecast_coverage_intervals(
                    now=now, interval_minutes=interval_minutes, resolver=resolver
                )
            )
        )
        for inv in self.inverters.values():
            coverages.append(
                int(
                    inv.forecast_coverage_intervals(
                        now=now, interval_minutes=interval_minutes, resolver=resolver
                    )
                )
            )
        # EVs do not contribute to horizon sizing today (no forecasts), only realtime gating.
        if not coverages:
            raise ValueError("No forecasts available to determine planning horizon")
        return int(min(coverages))

    def update(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        """Update all deferred boxes for this run (forecast alignment + realtime overrides)."""
        self.base_load.update(horizon=horizon, resolver=resolver)
        self.grid.update(horizon=horizon, resolver=resolver)
        for inv in self.inverters.values():
            inv.update(horizon=horizon, resolver=resolver)
        for ev in self.evs.values():
            ev.update(horizon=horizon, resolver=resolver)

    def build_snapshot(self, *, horizon: Horizon) -> ModelSnapshot:
        ctx = ModelContext(horizon=horizon)
        return ModelSnapshot(ctx=ctx, graph=self.graph)

    def build_timestep_plans(self, snapshot: ModelSnapshot) -> list[TimestepPlan]:
        horizon = snapshot.ctx.horizon

        base_load_series = self.base_load.base_load_kw.get_for_horizon(horizon)
        price_import = self.grid.price_import_raw.get_for_horizon(horizon)
        price_export = self.grid.price_export_raw.get_for_horizon(horizon)
        price_import_eff = self.grid.price_import_effective.get_for_horizon(horizon)
        price_export_eff = self.grid.price_export_effective.get_for_horizon(horizon)

        grid_iter: Iterator[GridTimestepPlan] = self.grid.iter_timestep_plan(snapshot)
        inverter_iters: dict[str, Iterator[InverterTimestepPlan]] = {
            inv_id: inv.iter_timestep_plan(snapshot) for inv_id, inv in self.inverters.items()
        }
        ev_iters: dict[str, Iterator[EvTimestepPlan]] = {
            ev_id: ev.iter_timestep_plan(snapshot) for ev_id, ev in self.evs.items()
        }

        cumulative_cost = 0.0
        timesteps: list[TimestepPlan] = []
        for t, slot in enumerate(horizon.slots):
            grid_plan = next(grid_iter)

            inverter_plans: dict[str, InverterTimestepPlan] = {
                inv_id: next(it) for inv_id, it in sorted(inverter_iters.items())
            }
            ev_plans: dict[str, EvTimestepPlan] = {
                ev_id: next(it) for ev_id, it in sorted(ev_iters.items())
            }

            base_kw = float(base_load_series[t]) if t < len(base_load_series) else 0.0
            ev_kw_total = sum(float(ev.charge_kw) for ev in ev_plans.values())
            total_kw = base_kw + ev_kw_total

            segment_cost = (
                float(grid_plan.import_kw) * float(price_import[t])
                - float(grid_plan.export_kw) * float(price_export[t])
            ) * float(slot.duration_h)
            cumulative_cost += float(segment_cost)

            timesteps.append(
                TimestepPlan(
                    index=t,
                    start=slot.start,
                    end=slot.end,
                    duration_s=(slot.end - slot.start).total_seconds(),
                    grid=grid_plan,
                    inverters=inverter_plans,
                    loads=LoadsTimestepPlan(
                        base_kw=base_kw,
                        evs=ev_plans,
                        total_kw=total_kw,
                    ),
                    economics=EconomicsTimestepPlan(
                        price_import=float(price_import[t]),
                        price_export=float(price_export[t]),
                        price_import_effective=float(price_import_eff[t]),
                        price_export_effective=float(price_export_eff[t]),
                        segment_cost=float(segment_cost),
                        cumulative_cost=float(cumulative_cost),
                    ),
                )
            )
        return timesteps
