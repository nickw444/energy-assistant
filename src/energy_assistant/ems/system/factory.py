from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from energy_assistant.ems.components.base_load import BaseLoadComponent
from energy_assistant.ems.components.ev import EvComponent
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.inverter import InverterComponent
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.forecast_alignment import (
    PowerForecastAligner,
    PriceForecastAligner,
    forecast_coverage_slots,
)
from energy_assistant.ems.forecast_multiplier import ForecastMultiplier
from energy_assistant.ems.horizon import Horizon, floor_to_interval_boundary
from energy_assistant.ems.pricing import PriceSeriesBuilder
from energy_assistant.ems.system.inputs import EmsInputs
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.ems.time_windows import TimeWindowMatcher
from energy_assistant.ems.topology.graph import EnergyGraphTemplate
from energy_assistant.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.config import AppConfig, EmsConfig
from energy_assistant.models.loads import ControlledEvLoad, LoadConfig
from energy_assistant.models.plant import PlantConfig, TimeWindow

logger = logging.getLogger(__name__)


class ResolvedForecasts:
    def __init__(
        self,
        *,
        grid_price_import: list[PriceForecastInterval],
        grid_price_export: list[PriceForecastInterval],
        load: list[PowerForecastInterval],
        inverters_pv: dict[str, list[PowerForecastInterval]],
        min_coverage_intervals: int,
    ) -> None:
        self.grid_price_import = grid_price_import
        self.grid_price_export = grid_price_export
        self.load = load
        self.inverters_pv = inverters_pv
        self.min_coverage_intervals = int(min_coverage_intervals)


class EmsSystemFactory:
    def __init__(self, app_config: AppConfig, *, resolver: ValueResolver) -> None:
        self._app_config = app_config
        self._plant: PlantConfig = app_config.plant
        self._loads: list[LoadConfig] = list(app_config.loads)
        self._resolver = resolver
        self._ems_config: EmsConfig = app_config.ems

        self._power_aligner = PowerForecastAligner()
        self._price_aligner = PriceForecastAligner()
        self._time_window_matcher = TimeWindowMatcher()
        self._price_series_builder = PriceSeriesBuilder(
            grid_price_bias_pct=self._plant.grid.grid_price_bias_pct,
            grid_price_risk=self._plant.grid.grid_price_risk,
        )

        self._system = self._build_system_template()

    @property
    def system(self) -> EmsSystem:
        return self._system

    def resolve_forecasts(self, *, now: datetime, interval_minutes: int) -> ResolvedForecasts:
        start = floor_to_interval_boundary(now, interval_minutes)

        load_intervals = self._resolver.resolve(self._plant.load.forecast)
        price_import_intervals = self._resolver.resolve(self._plant.grid.price_import_forecast)
        price_export_intervals = self._resolver.resolve(self._plant.grid.price_export_forecast)

        coverage_by_series: dict[str, int] = {}
        coverage_by_series["load"] = forecast_coverage_slots(
            start,
            interval_minutes,
            load_intervals,
            allow_first_slot_missing=True,
        )
        coverage_by_series["price_import"] = forecast_coverage_slots(
            start,
            interval_minutes,
            price_import_intervals,
            allow_first_slot_missing=True,
        )
        coverage_by_series["price_export"] = forecast_coverage_slots(
            start,
            interval_minutes,
            price_export_intervals,
            allow_first_slot_missing=True,
        )

        inverter_forecasts: dict[str, list[PowerForecastInterval]] = {}
        for inverter in self._plant.inverters:
            pv_intervals = self._resolver.resolve(inverter.pv.forecast)
            allow_first_slot_missing = inverter.pv.realtime_power is not None
            inverter_forecasts[inverter.id] = pv_intervals
            coverage_by_series[f"pv:{inverter.id}"] = forecast_coverage_slots(
                start,
                interval_minutes,
                pv_intervals,
                allow_first_slot_missing=allow_first_slot_missing,
            )

        if not coverage_by_series:
            raise ValueError("No forecasts available to determine planning horizon")

        min_coverage = min(coverage_by_series.values())
        limiting = sorted(
            name for name, length in coverage_by_series.items() if length == min_coverage
        )
        coverage_summary = ", ".join(
            f"{name}={length}" for name, length in sorted(coverage_by_series.items())
        )
        logger.info(
            "Forecast coverage (intervals): %s; limiting=%s",
            coverage_summary,
            ", ".join(limiting),
        )

        return ResolvedForecasts(
            grid_price_import=price_import_intervals,
            grid_price_export=price_export_intervals,
            load=load_intervals,
            inverters_pv=inverter_forecasts,
            min_coverage_intervals=min_coverage,
        )

    def build_inputs(self, *, horizon: Horizon, forecasts: ResolvedForecasts) -> EmsInputs:
        inputs = EmsInputs(horizon=horizon)

        # Base load aligned to slots with slot0 realtime override.
        realtime_load = float(self._resolver.resolve(self._plant.load.realtime_load_power))
        base_load_kw = self._power_aligner.align(
            horizon,
            forecasts.load,
            first_slot_override=realtime_load,
        )
        inputs.set_float_series("base_load_kw", base_load_kw)

        # Grid import/export prices (slot0 realtime override) and effective prices.
        realtime_import = float(self._resolver.resolve(self._plant.grid.realtime_price_import))
        realtime_export = float(self._resolver.resolve(self._plant.grid.realtime_price_export))
        price_import = self._price_aligner.align(
            horizon,
            forecasts.grid_price_import,
            first_slot_override=realtime_import,
        )
        price_export = self._price_aligner.align(
            horizon,
            forecasts.grid_price_export,
            first_slot_override=realtime_export,
        )
        inputs.set_float_series("price_import_raw", [float(x) for x in price_import])
        inputs.set_float_series("price_export_raw", [float(x) for x in price_export])

        price_series = self._price_series_builder.build_series(
            horizon=horizon,
            price_import=price_import,
            price_export=price_export,
        )
        price_import_eff = [float(x) for x in price_series.import_effective]
        price_export_eff = [float(x) for x in price_series.export_effective]
        inputs.set_float_series("price_import_effective", price_import_eff)
        inputs.set_float_series("price_export_effective", price_export_eff)

        # Export tie-break bonus when effective export price is exactly zero.
        export_bonus = 1e-4 if self._plant.grid.zero_price_export_preference == "export" else -1e-4
        export_eff_with_bonus = [
            export_bonus if abs(float(price_export_eff[t])) <= 1e-9 else float(price_export_eff[t])
            for t in horizon.T
        ]

        # Grid flow costs: import is +price; export is -revenue.
        inputs.set_float_series("grid_import_cost_per_kwh", price_import_eff)
        inputs.set_float_series(
            "grid_export_cost_per_kwh",
            [-float(x) for x in export_eff_with_bonus],
        )

        # Early-flow tie-break (tiny negative cost on any grid flow, stronger earlier).
        w_early = 1e-4
        early_cost = [(-w_early * (1.0 / (t + 1))) for t in horizon.T]
        inputs.set_float_series("grid_early_cost_per_kwh", early_cost)

        # Import forbidden windows -> allowed flags and soft limit series.
        import_allowed = self._resolve_import_allowed(horizon)
        inputs.set_bool_series("grid_import_allowed", import_allowed)
        inputs.set_float_series(
            "grid_import_limit_kw",
            [float(self._plant.grid.max_import_kw) * (1.0 if ok else 0.0) for ok in import_allowed],
        )

        # PV availability series per inverter (clamped, multiplier applied).
        for inverter in self._plant.inverters:
            inv_id = inverter.id
            pv_intervals = forecasts.inverters_pv[inv_id]
            realtime_pv = None
            if inverter.pv.realtime_power is not None:
                realtime_pv = float(self._resolver.resolve(inverter.pv.realtime_power))
            pv_series = self._power_aligner.align(
                horizon,
                pv_intervals,
                first_slot_override=realtime_pv,
            )
            pv_series = [max(0.0, min(float(v), float(inverter.peak_power_kw))) for v in pv_series]
            pv_series = ForecastMultiplier(inverter.pv.forecast_multiplier).apply(
                pv_series,
                skip_first_slot=realtime_pv is not None,
            )
            inputs.set_float_series(f"pv_available:{inv_id}", pv_series)

            if inverter.battery is None:
                continue
            battery = inverter.battery
            capacity_kwh = float(battery.capacity_kwh)
            initial_soc_pct = float(self._resolver.resolve(battery.state_of_charge_pct))
            initial_soc_kwh = capacity_kwh * initial_soc_pct / 100.0
            inputs.set_float(f"battery_initial_soc_kwh:{inv_id}", float(initial_soc_kwh))
            inputs.set_float_series(
                f"battery_charge_cost_per_kwh:{inv_id}",
                _constant_series(horizon, float(battery.charge_cost_per_kwh)),
            )
            inputs.set_float_series(
                f"battery_discharge_cost_per_kwh:{inv_id}",
                _constant_series(horizon, float(battery.discharge_cost_per_kwh)),
            )
            # Time-weighted throughput penalty series.
            w_batt_time = 1e-6
            inputs.set_float_series(
                f"battery_time_cost_per_kwh:{inv_id}",
                [float(w_batt_time) * float(t + 1) for t in horizon.T],
            )

        # EV per-load inputs.
        for load in self._loads:
            if not isinstance(load, ControlledEvLoad):
                continue
            ev_id = load.id
            connected = bool(self._resolver.resolve(load.connected))
            can_connect = True
            if load.can_connect is not None:
                can_connect = bool(self._resolver.resolve(load.can_connect))
            inputs.set_bool(f"ev_connected:{ev_id}", connected)
            realtime_power = float(self._resolver.resolve(load.realtime_power))
            inputs.set_float(f"ev_realtime_power_kw:{ev_id}", realtime_power)

            initial_soc_pct = float(self._resolver.resolve(load.state_of_charge_pct))
            capacity_kwh = float(load.energy_kwh)
            initial_soc_kwh = capacity_kwh * initial_soc_pct / 100.0
            initial_soc_kwh = max(0.0, min(capacity_kwh, initial_soc_kwh))
            inputs.set_float(f"ev_initial_soc_kwh:{ev_id}", float(initial_soc_kwh))

            gate_series = self._ev_connected_allowance(
                horizon=horizon,
                connected=connected,
                can_connect=can_connect,
                connect_times=load.allowed_connect_times,
                grace_minutes=load.connect_grace_minutes,
            )
            inputs.set_float_series(f"ev_gate:{ev_id}", gate_series)

        return inputs

    def _build_system_template(self) -> EmsSystem:
        graph = EnergyGraphTemplate()

        # Layer 1 components build the hidden topology template.
        switchboard = SwitchboardComponent(graph=graph)

        _ = BaseLoadComponent(graph=graph, switchboard_bus_id=switchboard.bus_id)

        grid_cfg = self._plant.grid
        grid = GridComponent(
            graph=graph,
            switchboard_bus_id=switchboard.bus_id,
            max_import_kw=float(grid_cfg.max_import_kw),
            max_export_kw=float(grid_cfg.max_export_kw),
        )

        inverters: dict[str, InverterComponent] = {}
        for inverter in self._plant.inverters:
            inv_id = inverter.id
            inverters[inv_id] = InverterComponent(
                graph=graph,
                switchboard_bus_id=switchboard.bus_id,
                inverter=inverter,
                grid_connection_id=grid.connection_id,
                grid_max_export_kw=float(grid_cfg.max_export_kw),
                terminal_soc_mode=self._ems_config.terminal_soc.mode,
                terminal_soc_penalty_per_kwh=self._ems_config.terminal_soc.penalty_per_kwh,
                battery_time_cost_key=f"battery_time_cost_per_kwh:{inv_id}",
                pv_available_key=f"pv_available:{inv_id}",
                battery_initial_soc_key=f"battery_initial_soc_kwh:{inv_id}",
                battery_charge_cost_key=f"battery_charge_cost_per_kwh:{inv_id}",
                battery_discharge_cost_key=f"battery_discharge_cost_per_kwh:{inv_id}",
            )

        evs: dict[str, EvComponent] = {}
        for load in self._loads:
            if not isinstance(load, ControlledEvLoad):
                continue
            ev_id = load.id
            evs[ev_id] = EvComponent(
                graph=graph,
                switchboard_bus_id=switchboard.bus_id,
                load=load,
                gate_series_key=f"ev_gate:{ev_id}",
                connected_bool_key=f"ev_connected:{ev_id}",
                realtime_power_kw_key=f"ev_realtime_power_kw:{ev_id}",
                initial_soc_kwh_key=f"ev_initial_soc_kwh:{ev_id}",
                grid_price_bias_pct=float(self._plant.grid.grid_price_bias_pct),
            )

        return EmsSystem(
            graph=graph,
            grid=grid,
            inverters=inverters,
            evs=evs,
        )

    def _resolve_import_allowed(self, horizon: Horizon) -> list[bool]:
        forbidden = self._plant.grid.import_forbidden_periods
        if not forbidden:
            return [True] * horizon.num_intervals
        matcher = self._time_window_matcher
        allowed: list[bool] = []
        for slot in horizon.slots:
            allowed.append(not matcher.matches(forbidden, slot.start))
        if len(allowed) != horizon.num_intervals:
            raise ValueError("import_allowed series length mismatch")
        return allowed

    def _ev_connected_allowance(
        self,
        *,
        horizon: Horizon,
        connected: bool,
        can_connect: bool,
        connect_times: Sequence[TimeWindow],
        grace_minutes: int,
    ) -> list[float]:
        if connected:
            return [1.0] * horizon.num_intervals
        if not can_connect:
            return [0.0] * horizon.num_intervals

        grace_end = horizon.now + timedelta(minutes=int(grace_minutes))
        matcher = self._time_window_matcher
        allowed: list[float] = []
        for slot in horizon.slots:
            if slot.start < grace_end:
                allowed.append(0.0)
                continue
            # Empty window list means "always allowed".
            if matcher.allows(connect_times, slot.start):
                allowed.append(1.0)
            else:
                allowed.append(0.0)
        return allowed


def _constant_series(horizon: Horizon, value: float) -> list[float]:
    return [float(value)] * horizon.num_intervals
