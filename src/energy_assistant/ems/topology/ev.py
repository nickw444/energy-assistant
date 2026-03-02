from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pulp

from energy_assistant.ems.topology.base import EnergyComponent

if TYPE_CHECKING:
    from energy_assistant.ems.horizon import Horizon
    from energy_assistant.ems.time_windows import TimeWindowMatcher
    from energy_assistant.lib.source_resolver.resolver import ValueResolver
    from energy_assistant.models.loads import ControlledEvLoad

_EV_SWITCH_ON_THRESHOLD_KW = 0.1


class EvComponent(EnergyComponent):
    def __init__(self, config: ControlledEvLoad, time_window_matcher: TimeWindowMatcher):
        super().__init__(id=config.id, name=config.name)
        self._config = config
        self._time_window_matcher = time_window_matcher

        # Variables
        self.P_ev_charge_kw: dict[int, pulp.LpVariable] = {}
        self.E_ev_kwh: dict[int, pulp.LpVariable] = {}
        self.Ev_charge_switch: dict[int, pulp.LpVariable] = {}
        self.Ev_incentive_segments: list[tuple[pulp.LpVariable, float]] = []
        self.connected: bool = False

    def resolve_data(
        self,
        resolver: ValueResolver,
        horizon_start: Any,
        interval_minutes: int,
    ) -> dict[str, Any]:
        # Most EV data is realtime
        return {}

    def add_variables(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        T = horizon.T
        ev_id = self.id
        capacity_kwh = float(self._config.energy_kwh)

        self.P_ev_charge_kw = pulp.LpVariable.dicts(
            f"P_ev_{ev_id}_charge_kw",
            T,
            lowBound=0,
            upBound=self._config.max_power_kw,
        )
        soc_indices = range(horizon.num_intervals + 1)
        self.E_ev_kwh = pulp.LpVariable.dicts(
            f"E_ev_{ev_id}_kwh",
            soc_indices,
            lowBound=0,
            upBound=capacity_kwh,
        )

        needs_charge_on = self._config.min_power_kw > 0 or self._config.switch_penalty > 0
        self._charge_on = None
        if needs_charge_on:
            self._charge_on = pulp.LpVariable.dicts(
                f"Ev_{ev_id}_charge_on",
                T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )

        if self._config.switch_penalty > 0 and self._charge_on is not None:
            self.Ev_charge_switch = pulp.LpVariable.dicts(
                f"Ev_{ev_id}_switch",
                T,
                lowBound=0,
                upBound=1,
            )

    def set_initial_conditions(self, problem: pulp.LpProblem, horizon: Horizon, resolver: ValueResolver) -> None:
        ev_id = self.id
        capacity_kwh = float(self._config.energy_kwh)
        initial_soc_pct = float(resolver.resolve(self._config.state_of_charge_pct))
        initial_soc_kwh = capacity_kwh * initial_soc_pct / 100.0
        initial_soc_kwh = max(0.0, min(capacity_kwh, initial_soc_kwh))

        problem += (
            self.E_ev_kwh[0] == initial_soc_kwh,
            f"ev_soc_initial_{ev_id}",
        )

        self.connected = bool(resolver.resolve(self._config.connected))
        can_connect = True
        if self._config.can_connect is not None:
            can_connect = bool(resolver.resolve(self._config.can_connect))

        connected_allow_by_slot = self._ev_connected_allowance(
            horizon=horizon,
            connected=self.connected,
            can_connect=can_connect,
        )

        # Switch penalty initial condition
        if self._config.switch_penalty > 0 and self._charge_on is not None:
            switch_threshold_kw = (
                self._config.min_power_kw
                if self._config.min_power_kw > 0
                else _EV_SWITCH_ON_THRESHOLD_KW
            )
            realtime_power = float(resolver.resolve(self._config.realtime_power))
            initial_on = 1.0 if self.connected and realtime_power >= switch_threshold_kw else 0.0
            problem += (
                self.Ev_charge_switch[0] >= self._charge_on[0] - initial_on,
                f"ev_switch_up_{ev_id}_t0",
            )
            problem += (
                self.Ev_charge_switch[0] >= initial_on - self._charge_on[0],
                f"ev_switch_down_{ev_id}_t0",
            )

        self._connected_allow_by_slot = connected_allow_by_slot

        # Build incentives here since it needs initial_soc_kwh
        self._build_ev_soc_incentives(problem, initial_soc_kwh)

    def _ev_connected_allowance(
        self,
        *,
        horizon: Horizon,
        connected: bool,
        can_connect: bool,
    ) -> list[float]:
        if connected:
            return [1.0] * horizon.num_intervals
        if not can_connect:
            return [0.0] * horizon.num_intervals

        grace_end = horizon.now + timedelta(minutes=self._config.connect_grace_minutes)
        allowed: list[float] = []
        for slot in horizon.slots:
            if slot.start < grace_end:
                allowed.append(0.0)
                continue
            if self._time_window_matcher.allows(self._config.allowed_connect_times, slot.start):
                allowed.append(1.0)
            else:
                allowed.append(0.0)
        return allowed

    def _build_ev_soc_incentives(
        self,
        problem: pulp.LpProblem,
        initial_soc_kwh: float,
    ) -> None:
        incentives = sorted(self._config.soc_incentives, key=lambda item: item.target_soc_pct)
        if not incentives:
            return

        ev_id = self.id
        capacity_kwh = float(self._config.energy_kwh)
        prev_target_kwh = 0.0

        for idx, incentive in enumerate(incentives):
            target_kwh = capacity_kwh * float(incentive.target_soc_pct) / 100.0
            available = max(0.0, target_kwh - max(prev_target_kwh, initial_soc_kwh))
            if available > 0:
                segment_var = pulp.LpVariable(
                    f"E_ev_{ev_id}_incentive_{idx}_kwh",
                    lowBound=0,
                    upBound=available,
                )
                self.Ev_incentive_segments.append((segment_var, float(incentive.incentive)))
            prev_target_kwh = target_kwh

        final_available = max(0.0, capacity_kwh - max(prev_target_kwh, initial_soc_kwh))
        if final_available > 0:
            segment_var = pulp.LpVariable(
                f"E_ev_{ev_id}_incentive_final_kwh",
                lowBound=0,
                upBound=final_available,
            )
            self.Ev_incentive_segments.append((segment_var, 0.0))

        terminal_soc = self.E_ev_kwh[len(self.P_ev_charge_kw)]
        problem += (
            pulp.lpSum(segment for segment, _ in self.Ev_incentive_segments) == terminal_soc - initial_soc_kwh,
            f"ev_incentive_total_{ev_id}",
        )

    def add_constraints(self, problem: pulp.LpProblem, horizon: Horizon) -> None:
        T = horizon.T
        ev_id = self.id
        for t in T:
            connected_allow = self._connected_allow_by_slot[t]
            problem += (
                self.P_ev_charge_kw[t] <= self._config.max_power_kw * connected_allow,
                f"ev_connected_limit_{ev_id}_t{t}",
            )
            if self._charge_on is not None:
                problem += (
                    self._charge_on[t] <= connected_allow,
                    f"ev_charge_on_connected_{ev_id}_t{t}",
                )
                min_power = self._config.min_power_kw
                if min_power <= 0 and self._config.switch_penalty > 0:
                    min_power = _EV_SWITCH_ON_THRESHOLD_KW
                if min_power > 0:
                    problem += (
                        self.P_ev_charge_kw[t] >= min_power * self._charge_on[t],
                        f"ev_charge_min_{ev_id}_t{t}",
                    )
                problem += (
                    self.P_ev_charge_kw[t] <= self._config.max_power_kw * self._charge_on[t],
                    f"ev_charge_max_{ev_id}_t{t}",
                )
            if self._config.switch_penalty > 0 and self._charge_on is not None and t > 0:
                problem += (
                    self.Ev_charge_switch[t] >= self._charge_on[t] - self._charge_on[t - 1],
                    f"ev_switch_up_{ev_id}_t{t}",
                )
                problem += (
                    self.Ev_charge_switch[t] >= self._charge_on[t - 1] - self._charge_on[t],
                    f"ev_switch_down_{ev_id}_t{t}",
                )
            # SoC dynamics
            problem += (
                self.E_ev_kwh[t + 1] == self.E_ev_kwh[t] + self.P_ev_charge_kw[t] * horizon.dt_hours(t),
                f"ev_soc_step_{ev_id}_t{t}",
            )

    def get_objective_terms(self, horizon: Horizon) -> pulp.LpAffineExpression:
        objective = pulp.LpAffineExpression()
        # EV incentives are biased by export bias in the original code.
        # This component doesn't know about export bias.
        # For now, we'll need to pass it in or handle it globally.
        return objective

    def get_pcc_load_kw(self, t: int) -> pulp.LpVariable:
        return self.P_ev_charge_kw[t]
