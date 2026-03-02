from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pulp

from energy_assistant.ems.forecast_alignment import PowerForecastAligner
from energy_assistant.ems.forecast_multiplier import ForecastMultiplier
from energy_assistant.ems.topology.base import EnergyComponent

if TYPE_CHECKING:
    from energy_assistant.ems.horizon import Horizon
    from energy_assistant.lib.source_resolver.resolver import ValueResolver
    from energy_assistant.models.config import EmsConfig
    from energy_assistant.models.plant import InverterConfig

_TERMINAL_SOC_REFERENCE_MINUTES = 1440.0


class InverterComponent(EnergyComponent):
    def __init__(self, config: InverterConfig, ems_config: EmsConfig):
        super().__init__(id=config.id, name=config.name)
        self._config = config
        self._ems_config = ems_config
        self._power_aligner = PowerForecastAligner()

        # Data
        self.pv_available_kw_series: list[float] = []

        # Variables
        self.P_pv_kw: dict[int, pulp.LpVariable] = {}
        self.P_inv_ac_net_kw: dict[int, pulp.LpVariable] = {}
        self.P_batt_charge_kw: dict[int, pulp.LpVariable] | None = None
        self.P_batt_discharge_kw: dict[int, pulp.LpVariable] | None = None
        self.E_batt_kwh: dict[int, pulp.LpVariable] | None = None
        self.E_batt_terminal_shortfall_kwh: pulp.LpVariable | None = None
        self.P_curtail_kw: dict[int, pulp.LpVariable] | None = None
        self.batt_charge_mode: dict[int, pulp.LpVariable] | None = None
        self.export_ok: dict[int, pulp.LpVariable] | None = None

    def resolve_data(
        self,
        resolver: ValueResolver,
        horizon_start: Any,
        interval_minutes: int,
    ) -> dict[str, Any]:
        pv_intervals = resolver.resolve(self._config.pv.forecast)
        return {"pv_intervals": pv_intervals}

    def align_data(self, horizon: Horizon, resolver: ValueResolver, resolved_forecasts: dict[str, Any]) -> None:
        realtime_pv = None
        if self._config.pv.realtime_power is not None:
            realtime_pv = resolver.resolve(self._config.pv.realtime_power)

        pv_intervals = resolved_forecasts["pv_intervals"]
        pv_available_kw_series = self._power_aligner.align(
            horizon,
            pv_intervals,
            first_slot_override=realtime_pv,
        )

        pv_available_kw_series = [
            max(0.0, min(float(value), self._config.peak_power_kw))
            for value in pv_available_kw_series
        ]

        self.pv_available_kw_series = ForecastMultiplier(self._config.pv.forecast_multiplier).apply(
            pv_available_kw_series,
            skip_first_slot=realtime_pv is not None,
        )

    def add_variables(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        T = horizon.T
        inv_id = self.id
        self.P_pv_kw = pulp.LpVariable.dicts(
            f"P_pv_{inv_id}_kw",
            T,
            lowBound=0,
            upBound=self._config.peak_power_kw,
        )
        self.P_inv_ac_net_kw = pulp.LpVariable.dicts(
            f"P_inv_{inv_id}_ac_net_kw",
            T,
            lowBound=-self._config.peak_power_kw,
            upBound=self._config.peak_power_kw,
        )

        curtailment = self._config.curtailment
        if curtailment is not None:
            self.P_curtail_kw = pulp.LpVariable.dicts(
                f"P_curtail_{inv_id}_kw",
                T,
                lowBound=0,
            )

        battery = self._config.battery
        if battery is not None:
            charge_limit = (
                battery.max_charge_kw
                if battery.max_charge_kw is not None
                else self._config.peak_power_kw
            )
            discharge_limit = (
                battery.max_discharge_kw
                if battery.max_discharge_kw is not None
                else self._config.peak_power_kw
            )
            discharge_limit = min(discharge_limit, self._config.peak_power_kw)

            soc_min_pct = battery.min_soc_pct
            soc_min_kwh = battery.capacity_kwh * soc_min_pct / 100.0
            soc_max_kwh = battery.capacity_kwh * battery.max_soc_pct / 100.0

            self.P_batt_charge_kw = pulp.LpVariable.dicts(
                f"P_batt_{inv_id}_charge_kw",
                T,
                lowBound=0,
                upBound=charge_limit,
            )
            self.P_batt_discharge_kw = pulp.LpVariable.dicts(
                f"P_batt_{inv_id}_discharge_kw",
                T,
                lowBound=0,
                upBound=discharge_limit,
            )
            self.batt_charge_mode = pulp.LpVariable.dicts(
                f"Batt_{inv_id}_charge_mode",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
            soc_indices = range(horizon.num_intervals + 1)
            self.E_batt_kwh = pulp.LpVariable.dicts(
                f"E_batt_{inv_id}_kwh",
                soc_indices,
                lowBound=soc_min_kwh,
                upBound=soc_max_kwh,
            )
            self.export_ok = pulp.LpVariable.dicts(
                f"Export_ok_{inv_id}",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
            if self._ems_config.terminal_soc.mode == "adaptive":
                self.E_batt_terminal_shortfall_kwh = pulp.LpVariable(
                    f"E_batt_{inv_id}_terminal_shortfall_kwh",
                    lowBound=0,
                )

    def add_constraints(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        T = horizon.T
        inv_id = self.id
        curtailment = self._config.curtailment

        # PV constraints
        if curtailment is None:
            for t in T:
                problem += (
                    self.P_pv_kw[t] == self.pv_available_kw_series[t],
                    f"inverter_pv_total_{inv_id}_t{t}",
                )
        elif curtailment == "binary":
            curtail_binary = pulp.LpVariable.dicts(
                f"Curtail_inv_{inv_id}",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
            for t in T:
                problem += (
                    self.P_pv_kw[t] == self.pv_available_kw_series[t] * (1 - curtail_binary[t]),
                    f"inverter_pv_binary_{inv_id}_t{t}",
                )
                if self.P_curtail_kw:
                    problem += (
                        self.P_curtail_kw[t] == self.pv_available_kw_series[t] - self.P_pv_kw[t],
                        f"inverter_curtail_power_{inv_id}_t{t}",
                    )
        else:  # load-aware
            for t in T:
                problem += (
                    self.P_pv_kw[t] <= self.pv_available_kw_series[t],
                    f"inverter_pv_max_{inv_id}_t{t}",
                )
                if self.P_curtail_kw:
                    problem += (
                        self.P_curtail_kw[t] == self.pv_available_kw_series[t] - self.P_pv_kw[t],
                        f"inverter_curtail_power_{inv_id}_t{t}",
                    )

        # Battery constraints
        battery = self._config.battery
        if battery is not None and self.P_batt_charge_kw and self.P_batt_discharge_kw and self.E_batt_kwh and self.batt_charge_mode and self.export_ok:
            charge_limit = self.P_batt_charge_kw[0].upBound
            discharge_limit = self.P_batt_discharge_kw[0].upBound
            soc_min_kwh = self.E_batt_kwh[0].lowBound
            soc_max_kwh = self.E_batt_kwh[0].upBound
            reserve_kwh = battery.capacity_kwh * battery.reserve_soc_pct / 100.0
            storage_efficiency = battery.storage_efficiency_pct / 100.0
            export_soc_m = soc_max_kwh - soc_min_kwh

            # Initial and terminal SoC handled in MILPBuilder or here
            # For now migrate it here for self-containment
            # We need ValueResolver to get initial SoC.
            # Wait, resolve_data should have it? No, initial SoC is realtime.

            for t in T:
                # Block grid export unless battery stays above reserve SoC for this slot.
                problem += (
                    self.E_batt_kwh[t] >= reserve_kwh - export_soc_m * (1 - self.export_ok[t]),
                    f"batt_export_reserve_start_{inv_id}_t{t}",
                )
                problem += (
                    self.E_batt_kwh[t + 1] >= reserve_kwh - export_soc_m * (1 - self.export_ok[t]),
                    f"batt_export_reserve_end_{inv_id}_t{t}",
                )
                # Export limit constraint needs grid.P_export, which is not here.
                # This suggests Grid and Inverters are coupled.
                # In MILPBuilder._build_inverters:
                # problem += (
                #    grid.P_export[t] <= self._plant.grid.max_export_kw * export_ok[t],
                #    f"grid_export_reserve_{inv_id}_t{t}",
                # )
                # We'll need a way to pass this back or handle it in a combined step.
                # For now, let's keep the local battery constraints.

                problem += (
                    self.P_batt_charge_kw[t] <= charge_limit * self.batt_charge_mode[t],
                    f"batt_charge_limit_{inv_id}_t{t}",
                )
                problem += (
                    self.P_batt_discharge_kw[t] <= discharge_limit * (1 - self.batt_charge_mode[t]),
                    f"batt_discharge_limit_{inv_id}_t{t}",
                )
                problem += (
                    self.P_inv_ac_net_kw[t] == self.P_pv_kw[t] + self.P_batt_discharge_kw[t] - self.P_batt_charge_kw[t],
                    f"inverter_ac_net_{inv_id}_t{t}",
                )
                problem += (
                    self.E_batt_kwh[t + 1]
                    == self.E_batt_kwh[t]
                    + (
                        self.P_batt_charge_kw[t] * storage_efficiency
                        - self.P_batt_discharge_kw[t] / storage_efficiency
                    )
                    * horizon.dt_hours(t),
                    f"batt_soc_step_{inv_id}_t{t}",
                )
        else:
            # No battery: AC net = PV
            for t in T:
                problem += (
                    self.P_inv_ac_net_kw[t] == self.P_pv_kw[t],
                    f"inverter_ac_net_{inv_id}_t{t}",
                )

    def set_initial_conditions(self, problem: pulp.LpProblem, horizon: Horizon, resolver: ValueResolver) -> None:
        inv_id = self.id
        battery = self._config.battery
        if battery is not None and self.E_batt_kwh:
            initial_soc_pct = resolver.resolve(battery.state_of_charge_pct)
            initial_soc_kwh = battery.capacity_kwh * float(initial_soc_pct) / 100.0
            problem += (
                self.E_batt_kwh[0] == initial_soc_kwh,
                f"batt_soc_initial_{inv_id}",
            )

            reserve_kwh = battery.capacity_kwh * battery.reserve_soc_pct / 100.0
            if self.E_batt_terminal_shortfall_kwh is not None:
                terminal_target_kwh = self._terminal_soc_target_kwh(
                    horizon,
                    initial_soc_kwh=initial_soc_kwh,
                    reserve_kwh=reserve_kwh,
                )
                problem += (
                    self.E_batt_kwh[horizon.num_intervals] + self.E_batt_terminal_shortfall_kwh
                    >= terminal_target_kwh,
                    f"batt_soc_terminal_{inv_id}",
                )
            else:
                problem += (
                    self.E_batt_kwh[horizon.num_intervals] >= initial_soc_kwh,
                    f"batt_soc_terminal_{inv_id}",
                )

    def _terminal_soc_return_ratio(self, horizon: Horizon) -> float:
        cfg = self._ems_config.terminal_soc
        if cfg.mode != "adaptive":
            return 1.0
        horizon_minutes = (horizon.slots[-1].end - horizon.start).total_seconds() / 60.0
        if horizon_minutes <= 0:
            return 1.0
        reference_minutes = _TERMINAL_SOC_REFERENCE_MINUTES
        shorter = min(horizon_minutes, reference_minutes)
        longer = max(horizon_minutes, reference_minutes)
        return shorter / longer

    def _terminal_soc_target_kwh(
        self,
        horizon: Horizon,
        *,
        initial_soc_kwh: float,
        reserve_kwh: float,
    ) -> float:
        ratio = self._terminal_soc_return_ratio(horizon)
        floor_kwh = min(initial_soc_kwh, reserve_kwh)
        return floor_kwh + ratio * (initial_soc_kwh - floor_kwh)

    def get_objective_terms(self, horizon: Horizon) -> pulp.LpAffineExpression:
        objective = pulp.LpAffineExpression()
        T = horizon.T
        battery = self._config.battery
        if battery is not None and self.P_batt_charge_kw and self.P_batt_discharge_kw:
            discharge_cost = battery.discharge_cost_per_kwh
            charge_cost = battery.charge_cost_per_kwh
            if discharge_cost > 0:
                objective += pulp.lpSum(
                    discharge_cost * self.P_batt_discharge_kw[t] * horizon.dt_hours(t)
                    for t in T
                )
            if charge_cost > 0:
                objective += pulp.lpSum(
                    charge_cost * self.P_batt_charge_kw[t] * horizon.dt_hours(t)
                    for t in T
                )
            # Tiny time-weighted throughput penalty
            w_batt_time = 1e-6
            objective += pulp.lpSum(
                w_batt_time
                * (self.P_batt_charge_kw[t] + self.P_batt_discharge_kw[t])
                * (t + 1)
                * horizon.dt_hours(t)
                for t in T
            )

            # Terminal SoC penalty/value
            # Penalty for adaptive mode
            if self.E_batt_terminal_shortfall_kwh is not None:
                # We need prices to calculate median/mean penalty.
                # This shows another dependency.
                pass

            # Reward for terminal SoC
            soc_value = battery.soc_value_per_kwh
            if soc_value is not None and soc_value > 0 and self.E_batt_kwh:
                terminal_idx = horizon.num_intervals
                objective += -soc_value * self.E_batt_kwh[terminal_idx]

        return objective

    def get_pcc_load_kw(self, t: int) -> pulp.LpAffineExpression | pulp.LpVariable | float:
        # Generation is negative load
        return -self.P_inv_ac_net_kw[t]
