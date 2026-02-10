from __future__ import annotations

import math
from collections.abc import Iterator

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintDescriptor, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import EvTimestepPlan
from energy_assistant.ems.time_windows import TimeWindowMatcher
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import Deferred, DeferredSeries
from energy_assistant.ems.topology.graph import EnergyGraph, GraphFragment
from energy_assistant.ems.topology.link_components import DirectionalLimit, LinkComponent
from energy_assistant.ems.topology.link_components.base import ConnectionBinding
from energy_assistant.ems.topology.nodes import StorageNode
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.models.loads import ControlledEvLoad, SocIncentive

_EV_SWITCH_ON_THRESHOLD_KW = 0.1


class EvChargeControl(LinkComponent):
    """Charge-on binary + optional switch penalty, implemented as a connection component."""

    def __init__(
        self,
        *,
        gate: DeferredSeries[float],
        connected: Deferred[bool],
        realtime_power_kw: Deferred[float],
        min_power_kw: float,
        max_power_kw: float,
        switch_penalty: float,
        name: str,
    ) -> None:
        self.gate = gate
        self.connected = connected
        self.realtime_power_kw = realtime_power_kw
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

    def charge_on(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        return connection.binary_series(f"Ev_{self.name}_charge_on")

    def switch(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        return connection.unit_series(f"Ev_{self.name}_switch")

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintDescriptor]:
        gate = self.gate.get_for_len(len(connection.T))
        for t, v in enumerate(gate):
            fv = float(v)
            if not math.isfinite(fv) or fv < -1e-9 or fv > 1.0 + 1e-9:
                raise ValueError(f"gate[{t}] must be in [0,1]; got {v}")

        P_charge = connection.P_a_to_b
        charge_on = self.charge_on(connection)

        constraints: list[ConstraintDescriptor] = []

        min_power = float(self.min_power_kw)
        if min_power <= 0 and self.switch_penalty > 0:
            min_power = _EV_SWITCH_ON_THRESHOLD_KW

        # Charge-on gating and min/max logic.
        for t in connection.T:
            constraints.append(
                ConstraintDescriptor(
                    f"ev_charge_on_gate_{self.name}_t{t}",
                    charge_on[t] <= float(gate[t]),
                )
            )
            if min_power > 0:
                constraints.append(
                    ConstraintDescriptor(
                        f"ev_charge_min_{self.name}_t{t}",
                        P_charge[t] >= float(min_power) * charge_on[t],
                    )
                )
            constraints.append(
                ConstraintDescriptor(
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
            is_initial_on = bool(self.connected.get()) and (
                float(self.realtime_power_kw.get()) >= threshold_kw
            )
            initial_on = (
                1.0
                if is_initial_on
                else 0.0
            )

            if 0 in connection.T:
                constraints.append(
                    ConstraintDescriptor(
                        f"ev_switch_up_{self.name}_t0",
                        switch[0] >= charge_on[0] - float(initial_on),
                    )
                )
                constraints.append(
                    ConstraintDescriptor(
                        f"ev_switch_down_{self.name}_t0",
                        switch[0] >= float(initial_on) - charge_on[0],
                    )
                )
            for t in connection.T:
                if t == 0:
                    continue
                constraints.append(
                    ConstraintDescriptor(
                        f"ev_switch_up_{self.name}_t{t}",
                        switch[t] >= charge_on[t] - charge_on[t - 1],
                    )
                )
                constraints.append(
                    ConstraintDescriptor(
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


class EvSocIncentivesFragment(GraphFragment):
    def __init__(
        self,
        *,
        ev_id: str,
        storage: StorageNode,
        initial_soc_kwh: Deferred[float],
        capacity_kwh: float,
        incentives: list[SocIncentive],
        grid_price_bias: float,
    ) -> None:
        self.ev_id = str(ev_id)
        self._storage = storage
        self._initial_soc_kwh = initial_soc_kwh
        self._capacity_kwh = float(capacity_kwh)
        self._incentives = list(incentives)
        self._grid_price_bias = float(grid_price_bias)

        self._constraints: list[ConstraintDescriptor] = []
        self._objective: pulp.LpAffineExpression = pulp.LpAffineExpression()

    def set_horizon(self, horizon: Horizon, graph: EnergyGraph) -> None:
        _ = graph
        node = self._storage
        incentives = sorted(self._incentives, key=lambda item: float(item.target_soc_pct))
        if not incentives:
            self._constraints = []
            self._objective = pulp.LpAffineExpression()
            return

        initial_soc_kwh = float(self._initial_soc_kwh.get())
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
            ConstraintDescriptor(
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

    @property
    def constraints(self) -> list[ConstraintDescriptor]:
        return list(self._constraints)

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return self._objective


class EvComponent:
    def __init__(
        self,
        *,
        graph: EnergyGraph,
        switchboard_bus_id: str,
        load: ControlledEvLoad,
        grid_price_bias_pct: float,
    ) -> None:
        self.id = str(load.id)
        self.name = str(load.name)
        self.capacity_kwh = float(load.energy_kwh)
        self.min_power_kw = float(load.min_power_kw)
        self.max_power_kw = float(load.max_power_kw)
        self.switch_penalty = float(load.switch_penalty)
        self.soc_incentives = list(load.soc_incentives)
        self._grid_price_bias = float(grid_price_bias_pct) / 100.0

        self._load = load
        self._matcher = TimeWindowMatcher()

        self._connected = Deferred[bool](name=f"ev_connected:{self.id}")
        self._realtime_power_kw = Deferred[float](name=f"ev_realtime_power_kw:{self.id}")
        self._initial_soc_kwh = Deferred[float](name=f"ev_initial_soc_kwh:{self.id}")
        self._gate = DeferredSeries[float](name=f"ev_gate:{self.id}")

        self.node_id = self.id
        self.connection_id = f"ev_{self.id}_link"

        self.storage = StorageNode(
            id=self.node_id,
            name=self.name,
            capacity_kwh=self.capacity_kwh,
            soc_min_kwh=0.0,
            soc_max_kwh=self.capacity_kwh,
            initial_soc_kwh=self._initial_soc_kwh,
            terminal_mode="none",
        )
        graph.add_storage(self.storage)

        self._charge_control = EvChargeControl(
            gate=self._gate,
            connected=self._connected,
            realtime_power_kw=self._realtime_power_kw,
            min_power_kw=self.min_power_kw,
            max_power_kw=self.max_power_kw,
            switch_penalty=self.switch_penalty,
            name=self.id,
        )

        # Connection convention: a_node is AC bus, b_node is EV storage (charge is a_to_b).
        self.connection = Connection(
            id=self.connection_id,
            a_node_id=str(switchboard_bus_id),
            b_node_id=self.node_id,
            link_components=[
                DirectionalLimit(max_a_to_b_kw=self.max_power_kw, max_b_to_a_kw=0.0),
                self._charge_control,
            ],
        )
        graph.add_connection(self.connection)

        if self.soc_incentives:
            graph.add_fragment(
                EvSocIncentivesFragment(
                    ev_id=self.id,
                    storage=self.storage,
                    initial_soc_kwh=self._initial_soc_kwh,
                    capacity_kwh=self.capacity_kwh,
                    incentives=self.soc_incentives,
                    grid_price_bias=self._grid_price_bias,
                )
            )

    def mark_for_hydration(self, resolver: ValueResolver) -> None:
        resolver.mark_for_hydration(self._load.connected)
        if self._load.can_connect is not None:
            resolver.mark_for_hydration(self._load.can_connect)
        resolver.mark_for_hydration(self._load.realtime_power)
        resolver.mark_for_hydration(self._load.state_of_charge_pct)

    def update(self, *, horizon: Horizon, resolver: ValueResolver) -> None:
        connected = bool(resolver.resolve(self._load.connected))
        can_connect = True
        if self._load.can_connect is not None:
            can_connect = bool(resolver.resolve(self._load.can_connect))

        self._connected.set(bool(connected))
        self._realtime_power_kw.set(float(resolver.resolve(self._load.realtime_power)))

        initial_soc_pct = float(resolver.resolve(self._load.state_of_charge_pct))
        initial_soc_kwh = self.capacity_kwh * initial_soc_pct / 100.0
        initial_soc_kwh = max(0.0, min(self.capacity_kwh, initial_soc_kwh))
        self._initial_soc_kwh.set(float(initial_soc_kwh))

        gate_series = self._connected_allowance(
            horizon=horizon,
            connected=connected,
            can_connect=can_connect,
        )
        self._gate.set(gate_series)

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

    def iter_timestep_plan(self, snapshot: ModelSnapshot) -> Iterator[EvTimestepPlan]:
        horizon = snapshot.ctx.horizon
        connected = bool(self._connected.get())
        for t in horizon.T:
            charge_kw = value_of(self.connection.P_a_to_b.get(t))
            soc_kwh = value_of(self.storage.E_by_i.get(t))
            soc_pct = (soc_kwh / float(self.capacity_kwh)) * 100.0 if self.capacity_kwh else None
            yield EvTimestepPlan(
                name=str(self.name),
                charge_kw=charge_kw,
                soc_kwh=soc_kwh,
                soc_pct=soc_pct,
                connected=connected,
            )
