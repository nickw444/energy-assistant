from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
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
    """Persistent EMS component definitions that build a run-scoped topology per solve."""

    def __init__(
        self,
        *,
        switchboard: SwitchboardComponent,
        base_load: BaseLoadComponent,
        grid: GridComponent,
        inverters: dict[str, InverterComponent],
        evs: dict[str, EvComponent],
    ) -> None:
        self.switchboard = switchboard
        self.base_load = base_load
        self.grid = grid
        self.inverters = dict(inverters)
        self.evs = dict(evs)

    @property
    def switchboard_bus_id(self) -> str:
        return str(self.switchboard.bus_id)

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

    def build_snapshot(self, *, horizon: Horizon, resolver: ValueResolver) -> ModelSnapshot:
        graph = EnergyGraph()
        graph.add_elements(self.switchboard.build(horizon=horizon))
        graph.add_elements(self.base_load.build(horizon=horizon, resolver=resolver))
        graph.add_elements(self.grid.build(horizon=horizon, resolver=resolver))

        grid_connection = self.grid.latest_connection()
        price_import_raw = self.grid.latest_price_import_raw()

        for inv in self.inverters.values():
            graph.add_elements(
                inv.build(
                    horizon=horizon,
                    resolver=resolver,
                    grid_connection=grid_connection,
                    price_import_raw=price_import_raw,
                )
            )

        for ev in self.evs.values():
            graph.add_elements(ev.build(horizon=horizon, resolver=resolver))

        ctx = ModelContext(horizon=horizon)
        return ModelSnapshot(ctx=ctx, graph=graph)

    def build_timestep_plans(self, snapshot: ModelSnapshot) -> list[TimestepPlan]:
        horizon = snapshot.ctx.horizon

        base_load_series = self.base_load.latest_base_load_kw()
        price_import = self.grid.latest_price_import_raw()
        price_export = self.grid.latest_price_export_raw()
        price_import_eff = self.grid.latest_price_import_effective()
        price_export_eff = self.grid.latest_price_export_effective()

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
