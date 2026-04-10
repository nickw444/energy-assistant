from __future__ import annotations

import math
from dataclasses import dataclass

import pulp

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.intent import build_load_controlled_ev_intent
from energy_assistant.ems.milp.context import ConstraintSpec, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import (
    LoadControlledEvComponentPlan,
)
from energy_assistant.ems.parameters import ScalarParameter, SeriesParameter
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.planning.time_windows import TimeWindowMatcher
from energy_assistant.ems.series import bool_series, interval_series_points, state_series_points
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.nodes import StorageNode
from energy_assistant.ems.topology.policies import (
    ConnectionPolicy,
    DirectionalLimit,
)
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
)
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import ControlledEvComponentConfig, SocIncentive

_EV_SWITCH_ON_THRESHOLD_KW = 0.1


@dataclass(frozen=True, slots=True)
class EvSolveState:
    connected: bool
    storage: StorageNode
    connection: Connection
    gate_series: list[float]


class EvComponent:
    def __init__(
        self,
        *,
        component_id: str,
        switchboard_bus_id: str,
        load: ControlledEvComponentConfig,
        grid_export_bias_pct: float,
        time_window_matcher: TimeWindowMatcher,
    ) -> None:
        self.id = str(component_id)
        self.name = str(load.name)
        self.capacity_kwh = float(load.energy_kwh)
        self.min_power_kw = float(load.min_power_kw)
        self.max_power_kw = float(load.max_power_kw)
        self.switch_penalty = float(load.switch_penalty)
        self.soc_incentives = list(load.soc_incentives)
        self._grid_price_bias = float(grid_export_bias_pct) / 100.0

        self._load = load
        self._matcher = time_window_matcher

        self.node_id = self.id
        self.connection_id = f"ev_{self.id}_link"
        self.switchboard_bus_id = str(switchboard_bus_id)

        self._connected = ScalarParameter[bool](f"{self.id}_connected")
        self._realtime_power_kw = ScalarParameter[float](f"{self.id}_realtime_power_kw")
        self._initial_soc_kwh = ScalarParameter[float](f"{self.id}_initial_soc_kwh")
        self._gate_series = SeriesParameter[float](f"{self.id}_gate_series")

    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        connected = inputs.scalar_bool(self._load.connected.key)
        can_connect = True
        if self._load.can_connect is not None:
            can_connect = inputs.scalar_bool(self._load.can_connect.key)

        realtime_power_kw = inputs.scalar_float(
            self._load.realtime_power.key,
            kind=InputValueKind.POWER,
        )

        initial_soc_pct = inputs.scalar_float(
            self._load.state_of_charge_pct.key,
            kind=InputValueKind.PERCENTAGE,
        )
        initial_soc_kwh = self.capacity_kwh * initial_soc_pct / 100.0
        initial_soc_kwh = max(0.0, min(self.capacity_kwh, initial_soc_kwh))

        gate_series = self._connected_allowance(
            horizon=horizon,
            connected=connected,
            can_connect=can_connect,
        )
        self._connected.set(connected)
        self._realtime_power_kw.set(realtime_power_kw)
        self._initial_soc_kwh.set(max(0.0, min(self.capacity_kwh, initial_soc_kwh)))
        self._gate_series.set(gate_series)

    def graph_elements(self, *, horizon: Horizon) -> tuple[list[GraphElement], EvSolveState]:
        connected = self._connected.get()
        realtime_power_kw = self._realtime_power_kw.get()
        initial_soc_kwh = self._initial_soc_kwh.get()
        gate_series = self._gate_series.get()

        storage = StorageNode(
            horizon=horizon,
            id=self.node_id,
            name=self.name,
            capacity_kwh=self.capacity_kwh,
            soc_min_kwh=0.0,
            soc_max_kwh=self.capacity_kwh,
            initial_soc_kwh=initial_soc_kwh,
            terminal_mode="none",
        )

        charge_control = EvChargeControl(
            gate=gate_series,
            connected=connected,
            realtime_power_kw=realtime_power_kw,
            min_power_kw=self.min_power_kw,
            max_power_kw=self.max_power_kw,
            switch_penalty=self.switch_penalty,
            name=self.id,
        )

        # Connection convention: a_node is AC bus, b_node is EV storage (charge is a_to_b).
        connection = Connection(
            horizon=horizon,
            id=self.connection_id,
            a_node_id=self.switchboard_bus_id,
            b_node_id=self.node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=self.max_power_kw,
                    max_b_to_a_kw=0.0,
                ),
                "charge_control": charge_control,
            },
        )

        elements: list[GraphElement] = [storage, connection]

        if self.soc_incentives:
            elements.append(
                EvSocIncentivesFragment(
                    horizon=horizon,
                    ev_id=self.id,
                    storage=storage,
                    initial_soc_kwh=initial_soc_kwh,
                    capacity_kwh=self.capacity_kwh,
                    incentives=self.soc_incentives,
                    grid_price_bias=self._grid_price_bias,
                )
            )

        solve_state = EvSolveState(
            connected=connected,
            storage=storage,
            connection=connection,
            gate_series=gate_series,
        )
        return elements, solve_state

    def _connected_allowance(
        self,
        *,
        horizon: Horizon,
        connected: bool,
        can_connect: bool,
    ) -> list[float]:
        if connected:
            return [1.0] * int(horizon.num_intervals)
        if not can_connect:
            return [0.0] * int(horizon.num_intervals)

        from datetime import timedelta

        grace_end = horizon.now + timedelta(minutes=int(self._load.connect_grace_minutes))
        allowed: list[float] = []
        for slot in horizon.slots:
            if slot.start < grace_end:
                allowed.append(0.0)
                continue
            # Empty window list means "always allowed".
            if self._matcher.allows(self._load.allowed_connect_times, slot.start):
                allowed.append(1.0)
            else:
                allowed.append(0.0)
        return allowed

    def build_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: EvSolveState,
    ) -> LoadControlledEvComponentPlan:
        horizon = snapshot.ctx.horizon
        storage = solve_state.storage
        connection = solve_state.connection
        charge_kw = [value_of(connection.flow_into_node(self.node_id).get(t)) for t in horizon.T]
        soc_kwh = [value_of(storage.E_by_i.get(t)) for t in range(horizon.num_intervals + 1)]
        soc_pct = [
            (float(value) / float(self.capacity_kwh)) * 100.0 if self.capacity_kwh else 0.0
            for value in soc_kwh
        ]
        connected = [bool(solve_state.connected)] * horizon.num_intervals
        charge_allowed = [value > 0 for value in solve_state.gate_series]
        first_charge_kw = float(charge_kw[0]) if charge_kw else 0.0
        first_connected = bool(connected[0]) if connected else False
        return LoadControlledEvComponentPlan(
            charge_kw=interval_series_points(horizon, charge_kw),
            soc_kwh=state_series_points(horizon, soc_kwh),
            soc_pct=state_series_points(horizon, soc_pct),
            connected=interval_series_points(horizon, bool_series(connected)),
            charge_allowed=interval_series_points(horizon, bool_series(charge_allowed)),
            intent=build_load_controlled_ev_intent(
                charge_kw=first_charge_kw,
                connected=first_connected,
                ev_config=self._load,
            ),
        )


class EvChargeControl(ConnectionPolicy):
    """Charge-on binary + optional switch penalty, implemented as a connection component."""

    def __init__(
        self,
        *,
        gate: list[float],
        connected: bool,
        realtime_power_kw: float,
        min_power_kw: float,
        max_power_kw: float,
        switch_penalty: float,
        name: str,
    ) -> None:
        self.gate = [float(v) for v in gate]
        self.connected = bool(connected)
        self.realtime_power_kw = float(realtime_power_kw)
        self.min_power_kw = float(min_power_kw)
        self.max_power_kw = float(max_power_kw)
        self.switch_penalty = float(switch_penalty)
        self.name = str(name)

        if self.max_power_kw < 0:
            raise ValueError("max_power_kw must be >= 0")
        if self.min_power_kw < 0:
            raise ValueError("min_power_kw must be >= 0")
        if self.switch_penalty < 0:
            raise ValueError("switch_penalty must be >= 0")
        self._charge_on_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}
        self._switch_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}

    def charge_on(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        if connection.id not in self._charge_on_by_connection:
            self._charge_on_by_connection[connection.id] = pulp.LpVariable.dicts(
                f"Ev_{self.name}_charge_on_{connection.id}",
                connection.horizon.T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        return self._charge_on_by_connection[connection.id]

    def switch(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        if connection.id not in self._switch_by_connection:
            self._switch_by_connection[connection.id] = pulp.LpVariable.dicts(
                f"Ev_{self.name}_switch_{connection.id}",
                connection.horizon.T,
                lowBound=0,
                upBound=1,
            )
        return self._switch_by_connection[connection.id]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        if len(self.gate) != len(connection.horizon.T):
            raise ValueError(
                f"EV gate series length {len(self.gate)} does not match connection horizon "
                f"length {len(connection.horizon.T)}"
            )
        for t, v in enumerate(self.gate):
            fv = float(v)
            if not math.isfinite(fv) or fv < -1e-9 or fv > 1.0 + 1e-9:
                raise ValueError(f"gate[{t}] must be in [0,1]; got {v}")

        P_charge = connection.flow_out_ab
        charge_on = self.charge_on(connection)

        constraints = list(self._passthrough_constraints(connection))

        min_power = float(self.min_power_kw)
        if min_power <= 0 and self.switch_penalty > 0:
            min_power = _EV_SWITCH_ON_THRESHOLD_KW

        # Charge-on gating and min/max logic.
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"ev_charge_on_gate_{self.name}_t{t}",
                    charge_on[t] <= float(self.gate[t]),
                )
            )
            if min_power > 0:
                constraints.append(
                    ConstraintSpec(
                        f"ev_charge_min_{self.name}_t{t}",
                        P_charge[t] >= float(min_power) * charge_on[t],
                    )
                )
            constraints.append(
                ConstraintSpec(
                    f"ev_charge_max_{self.name}_t{t}",
                    P_charge[t] <= float(self.max_power_kw) * charge_on[t],
                )
            )

        # Switch penalty (absolute on/off transitions), including t0 seeding from realtime state.
        if self.switch_penalty > 0:
            switch = self.switch(connection)
            threshold_kw = (
                float(self.min_power_kw) if self.min_power_kw > 0 else _EV_SWITCH_ON_THRESHOLD_KW
            )
            is_initial_on = bool(self.connected) and (float(self.realtime_power_kw) >= threshold_kw)
            initial_on = 1.0 if is_initial_on else 0.0

            if 0 in connection.horizon.T:
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_up_{self.name}_t0",
                        switch[0] >= charge_on[0] - float(initial_on),
                    )
                )
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_down_{self.name}_t0",
                        switch[0] >= float(initial_on) - charge_on[0],
                    )
                )
            for t in connection.horizon.T:
                if t == 0:
                    continue
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_up_{self.name}_t{t}",
                        switch[t] >= charge_on[t] - charge_on[t - 1],
                    )
                )
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_down_{self.name}_t{t}",
                        switch[t] >= charge_on[t - 1] - charge_on[t],
                    )
                )

        return constraints

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        if self.switch_penalty <= 0:
            return pulp.LpAffineExpression()
        switch = self.switch(connection)
        return float(self.switch_penalty) * pulp.lpSum(switch.values())


class EvSocIncentivesFragment:
    def __init__(
        self,
        *,
        horizon: Horizon,
        ev_id: str,
        storage: StorageNode,
        initial_soc_kwh: float,
        capacity_kwh: float,
        incentives: list[SocIncentive],
        grid_price_bias: float,
    ) -> None:
        self._horizon = horizon
        self.ev_id = str(ev_id)
        self._storage = storage
        self._initial_soc_kwh = float(initial_soc_kwh)
        self._capacity_kwh = float(capacity_kwh)
        self._incentives = list(incentives)
        self._grid_price_bias = float(grid_price_bias)

        self._built = False
        self._constraints: list[ConstraintSpec] = []
        self._objective: pulp.LpAffineExpression = pulp.LpAffineExpression()

    def _ensure_built(self) -> None:
        if self._built:
            return

        horizon = self._horizon
        node = self._storage
        incentives = sorted(self._incentives, key=lambda item: float(item.target_soc_pct))
        if not incentives:
            self._constraints = []
            self._objective = pulp.LpAffineExpression()
            self._built = True
            return

        initial_soc_kwh = float(self._initial_soc_kwh)
        capacity_kwh = float(self._capacity_kwh)
        terminal_soc = node.E_by_i[int(horizon.num_intervals)]

        segments: list[tuple[pulp.LpVariable, float]] = []
        prev_target_kwh = 0.0
        for idx, incentive in enumerate(incentives):
            target_pct = float(incentive.target_soc_pct)
            incentive_value = float(incentive.incentive)
            target_kwh = capacity_kwh * target_pct / 100.0
            if target_kwh < prev_target_kwh:
                raise ValueError("EV incentive targets must be non-decreasing")
            available = max(0.0, target_kwh - max(prev_target_kwh, initial_soc_kwh))
            if available > 0:
                seg = pulp.LpVariable(
                    f"E_ev_{self.ev_id}_incentive_{idx}_kwh",
                    lowBound=0,
                    upBound=float(available),
                )
                segments.append((seg, incentive_value))
            prev_target_kwh = target_kwh

        final_available = max(0.0, capacity_kwh - max(prev_target_kwh, initial_soc_kwh))
        if final_available > 0:
            seg = pulp.LpVariable(
                f"E_ev_{self.ev_id}_incentive_final_kwh",
                lowBound=0,
                upBound=float(final_available),
            )
            segments.append((seg, 0.0))

        self._constraints = [
            ConstraintSpec(
                f"ev_incentive_total_{self.ev_id}",
                pulp.lpSum(seg for seg, _ in segments) == terminal_soc - float(initial_soc_kwh),
            )
        ]

        def _apply_export_bias(value: float) -> float:
            bias = float(self._grid_price_bias)
            if bias == 0:
                return value
            if value >= 0:
                return value * (1.0 - bias)
            return value * (1.0 + bias)

        objective_expr = pulp.lpSum(
            -_apply_export_bias(float(incentive)) * seg for seg, incentive in segments
        )
        self._objective = objective_expr if segments else pulp.LpAffineExpression()
        self._built = True

    @property
    def constraints(self) -> list[ConstraintSpec]:
        self._ensure_built()
        return list(self._constraints)

    @property
    def objective(self) -> pulp.LpAffineExpression:
        self._ensure_built()
        return self._objective
