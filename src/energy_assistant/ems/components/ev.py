from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import pulp

from energy_assistant.ems.components.component import EmsComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ConstraintSpec, value_of
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.models import (
    EvSoftDeadlinePlan,
    LoadControlledEvComponentPlan,
)
from energy_assistant.ems.series import bool_series, interval_series_points, state_series_points
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import GraphElement
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node, StorageNode
from energy_assistant.ems.topology.policies import (
    DirectionalLimit,
    Passthrough,
)
from energy_assistant.ems.topology.policies.connection_policy import (
    ConnectionBinding,
)
from energy_assistant.models.inputs import InputValueKind
from energy_assistant.models.plant import ControlledEvComponentConfig

_EV_SWITCH_ON_THRESHOLD_KW = 0.1


@dataclass(frozen=True, slots=True)
class EvSolveState:
    connected: bool
    charger_node_id: NodeId
    charge_connection: Connection
    storages: tuple[StorageNode, ...]
    gate_series: list[float]
    soft_deadline_fragment: EvSoftDeadlineFragment | None


@dataclass(frozen=True, slots=True)
class EvStorageSegment:
    node: StorageNode
    connection: Connection


class EvComponent(EmsComponent[EvSolveState, LoadControlledEvComponentPlan]):
    def __init__(
        self,
        *,
        component_id: str,
        switchboard: SwitchboardComponent,
        load: ControlledEvComponentConfig,
        grid_export_bias_pct: float,
        time_window_matcher: TimeWindowMatcher,
    ) -> None:
        _ = grid_export_bias_pct
        self.id = component_id
        self.switchboard = switchboard
        self._config = load
        self._matcher = time_window_matcher

        self.name = self._config.name
        self.node_id = NodeId(component_id)

    def _initial_soc_kwh_from_inputs(
        self,
        *,
        inputs: AppliedInputRegistry,
    ) -> float:
        initial_soc_pct = inputs.scalar_float(
            self._config.state_of_charge_pct.key,
            kind=InputValueKind.PERCENTAGE,
        )
        initial_soc_kwh = self._config.energy_kwh * initial_soc_pct / 100.0
        return max(0.0, min(self._config.energy_kwh, initial_soc_kwh))

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], EvSolveState]:
        _ = build_ctx
        connected = inputs.scalar_bool(self._config.connected.key)
        can_connect = True
        if self._config.can_connect is not None:
            can_connect = inputs.scalar_bool(self._config.can_connect.key)

        realtime_power_kw = inputs.scalar_float(
            self._config.realtime_power.key,
            kind=InputValueKind.POWER,
        )
        initial_soc_kwh = self._initial_soc_kwh_from_inputs(inputs=inputs)
        gate_series = self._connected_allowance(
            horizon=horizon,
            connected=connected,
            can_connect=can_connect,
        )

        charger_node_id = NodeId(f"{self.id}_charger")
        segments = self._create_segmented_storage(
            horizon=horizon,
            initial_soc_kwh=initial_soc_kwh,
            charger_node_id=charger_node_id,
        )
        charger = Node(
            horizon=horizon,
            id=charger_node_id,
            name=f"{self.name} charger",
            node_role="bus",
        )
        charge_control = EvChargeControl(
            gate=gate_series,
            connected=connected,
            realtime_power_kw=realtime_power_kw,
            min_power_kw=self._config.min_power_kw,
            max_power_kw=self._config.max_power_kw,
            switch_penalty=self._config.switch_penalty,
            name=self.id,
        )
        charge_connection = Connection(
            horizon=horizon,
            id=f"ev_{self.id}_charger_link",
            a_node_id=self.switchboard.bus_id,
            b_node_id=charger_node_id,
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=self._config.max_power_kw,
                    max_b_to_a_kw=0.0,
                ),
                "charge_control": charge_control,
            },
        )
        storages = tuple(segment.node for segment in segments)
        soft_deadline_fragment = self._build_soft_deadline_fragment(
            horizon=horizon,
            storages=storages,
        )
        elements: list[GraphElement] = [
            charger,
            *(segment.node for segment in segments),
            charge_connection,
            *(segment.connection for segment in segments),
            *([soft_deadline_fragment] if soft_deadline_fragment is not None else []),
        ]

        solve_state = EvSolveState(
            connected=connected,
            charger_node_id=charger_node_id,
            charge_connection=charge_connection,
            storages=storages,
            gate_series=gate_series,
            soft_deadline_fragment=soft_deadline_fragment,
        )
        return elements, solve_state

    def _create_segmented_storage(
        self,
        *,
        horizon: Horizon,
        initial_soc_kwh: float,
        charger_node_id: NodeId,
    ) -> tuple[EvStorageSegment, ...]:
        segment_specs: list[tuple[float, float, float]] = []
        prev_target_kwh = 0.0
        incentives = sorted(self._config.soc_incentives, key=lambda item: item.target_soc_pct)
        for incentive in incentives:
            target_kwh = self._config.energy_kwh * incentive.target_soc_pct / 100.0
            if target_kwh < prev_target_kwh:
                raise ValueError("EV incentive targets must be non-decreasing")
            if target_kwh > prev_target_kwh:
                segment_specs.append((prev_target_kwh, target_kwh, incentive.incentive))
            prev_target_kwh = target_kwh

        final_capacity_kwh = self._config.energy_kwh - prev_target_kwh
        if final_capacity_kwh > 0 or not segment_specs:
            segment_specs.append((prev_target_kwh, self._config.energy_kwh, 0.0))

        segments: list[EvStorageSegment] = []
        for idx, (start_kwh, end_kwh, incentive) in enumerate(segment_specs):
            capacity_kwh = end_kwh - start_kwh
            initial_segment_kwh = max(
                0.0,
                min(initial_soc_kwh, end_kwh) - start_kwh,
            )
            segment_name = str(idx)
            node_id = NodeId(f"{self.id}_segment_{segment_name}")
            storage = StorageNode(
                horizon=horizon,
                id=node_id,
                name=f"{self.name} segment {segment_name}",
                capacity_kwh=capacity_kwh,
                soc_min_kwh=0.0,
                soc_max_kwh=capacity_kwh,
                initial_soc_kwh=initial_segment_kwh,
                stored_energy_value_per_kwh=incentive,
            )
            connection = Connection(
                horizon=horizon,
                id=f"ev_{self.id}_segment_{segment_name}_link",
                a_node_id=charger_node_id,
                b_node_id=node_id,
                policies={
                    "directional_limit": DirectionalLimit(
                        max_a_to_b_kw=self._config.max_power_kw,
                        max_b_to_a_kw=0.0,
                    ),
                },
            )
            segments.append(EvStorageSegment(node=storage, connection=connection))
        return tuple(segments)

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

        grace_end = horizon.now + timedelta(minutes=int(self._config.connect_grace_minutes))
        allowed: list[float] = []
        for slot in horizon.slots:
            if slot.start < grace_end:
                allowed.append(0.0)
                continue
            # Empty window list means "always allowed".
            if self._matcher.allows(self._config.allowed_connect_times, slot.start):
                allowed.append(1.0)
            else:
                allowed.append(0.0)
        return allowed

    def _resolve_soft_deadlines(self, *, horizon: Horizon) -> tuple[EvSoftDeadlineSpec, ...]:
        if not self._config.soft_deadlines:
            return ()
        deadlines: list[EvSoftDeadlineSpec] = []
        for configured in self._config.soft_deadlines:
            deadline_at = self._next_deadline_datetime(
                start=horizon.start,
                by_time=configured.by_time,
            )
            deadline_index = self._deadline_state_index(
                horizon=horizon,
                deadline_at=deadline_at,
            )
            deadlines.append(
                EvSoftDeadlineSpec(
                    by_time=configured.by_time,
                    deadline_at=deadline_at,
                    deadline_index=deadline_index,
                    target_soc_pct=configured.target_soc_pct,
                    target_kwh=self._config.energy_kwh * configured.target_soc_pct / 100.0,
                    shortfall_penalty=configured.shortfall_penalty,
                )
            )
        return tuple(deadlines)

    def _build_soft_deadline_fragment(
        self,
        *,
        horizon: Horizon,
        storages: tuple[StorageNode, ...],
    ) -> EvSoftDeadlineFragment | None:
        soft_deadlines = self._resolve_soft_deadlines(horizon=horizon)
        if not soft_deadlines:
            return None
        return EvSoftDeadlineFragment(
            storages=storages,
            deadlines=soft_deadlines,
            name=self.id,
        )

    def _next_deadline_datetime(self, *, start: datetime, by_time: str) -> datetime:
        hour_text, minute_text = by_time.split(":")
        candidate = start.replace(
            hour=int(hour_text),
            minute=int(minute_text),
            second=0,
            microsecond=0,
        )
        if candidate <= start:
            candidate = candidate + timedelta(days=1)
        return candidate

    def _deadline_state_index(self, *, horizon: Horizon, deadline_at: datetime) -> int:
        for idx, slot in enumerate(horizon.slots, start=1):
            if slot.end >= deadline_at:
                return idx
        return horizon.num_intervals

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: EvSolveState,
        plan_ctx: PlanContext,
    ) -> LoadControlledEvComponentPlan:
        _ = plan_ctx
        horizon = snapshot.ctx.horizon
        charge_kw = [
            value_of(solve_state.charge_connection.flow_into_node(solve_state.charger_node_id).get(t))
            for t in horizon.T
        ]
        soc_kwh = [
            sum(value_of(storage.E_by_i.get(t)) for storage in solve_state.storages)
            for t in range(horizon.num_intervals + 1)
        ]
        soc_pct = [
            (value / self._config.energy_kwh) * 100.0 if self._config.energy_kwh else 0.0
            for value in soc_kwh
        ]
        connected = [bool(solve_state.connected)] * horizon.num_intervals
        charge_allowed = [value > 0 for value in solve_state.gate_series]
        soft_deadlines: list[EvSoftDeadlinePlan] = []
        if solve_state.soft_deadline_fragment is not None:
            for idx, deadline in enumerate(solve_state.soft_deadline_fragment.deadlines):
                achieved_kwh = soc_kwh[deadline.deadline_index]
                achieved_soc_pct = (
                    achieved_kwh / self._config.energy_kwh * 100.0
                    if self._config.energy_kwh > 0
                    else 0.0
                )
                soft_deadlines.append(
                    EvSoftDeadlinePlan(
                        by_time=deadline.by_time,
                        deadline_at=deadline.deadline_at,
                        target_soc_pct=deadline.target_soc_pct,
                        target_kwh=deadline.target_kwh,
                        achieved_soc_pct=achieved_soc_pct,
                        achieved_kwh=achieved_kwh,
                        shortfall_kwh=solve_state.soft_deadline_fragment.shortfall_value(idx),
                    )
                )
        return LoadControlledEvComponentPlan(
            charge_kw=interval_series_points(horizon, charge_kw),
            soc_kwh=state_series_points(horizon, soc_kwh),
            soc_pct=state_series_points(horizon, soc_pct),
            connected=interval_series_points(horizon, bool_series(connected)),
            charge_allowed=interval_series_points(horizon, bool_series(charge_allowed)),
            soft_deadlines=soft_deadlines,
        )


class EvChargeControl(Passthrough):
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
        self._gate = list(gate)
        self._connected = bool(connected)
        self._realtime_power_kw = realtime_power_kw
        self._min_power_kw = min_power_kw
        self._max_power_kw = max_power_kw
        self._switch_penalty = switch_penalty
        self._name = name

        if self._max_power_kw < 0:
            raise ValueError("max_power_kw must be >= 0")
        if self._min_power_kw < 0:
            raise ValueError("min_power_kw must be >= 0")
        if self._switch_penalty < 0:
            raise ValueError("switch_penalty must be >= 0")
        self._charge_on_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}
        self._switch_by_connection: dict[str, dict[int, pulp.LpVariable]] = {}

    def _charge_on(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        if connection.id not in self._charge_on_by_connection:
            self._charge_on_by_connection[connection.id] = pulp.LpVariable.dicts(
                f"Ev_{self._name}_charge_on_{connection.id}",
                connection.horizon.T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        return self._charge_on_by_connection[connection.id]

    def _switch(self, connection: ConnectionBinding) -> dict[int, pulp.LpVariable]:
        if connection.id not in self._switch_by_connection:
            self._switch_by_connection[connection.id] = pulp.LpVariable.dicts(
                f"Ev_{self._name}_switch_{connection.id}",
                connection.horizon.T,
                lowBound=0,
                upBound=1,
            )
        return self._switch_by_connection[connection.id]

    def constraints(self, connection: ConnectionBinding) -> list[ConstraintSpec]:
        if len(self._gate) != len(connection.horizon.T):
            raise ValueError(
                f"EV gate series length {len(self._gate)} does not match connection horizon "
                f"length {len(connection.horizon.T)}"
            )
        for t, v in enumerate(self._gate):
            if not math.isfinite(v) or v < -1e-9 or v > 1.0 + 1e-9:
                raise ValueError(f"gate[{t}] must be in [0,1]; got {v}")

        P_charge = connection.flow_out_ab
        charge_on = self._charge_on(connection)

        constraints = list(self._passthrough_constraints(connection))

        min_power = self._min_power_kw
        if min_power <= 0 and self._switch_penalty > 0:
            min_power = _EV_SWITCH_ON_THRESHOLD_KW

        # Charge-on gating and min/max logic.
        for t in connection.horizon.T:
            constraints.append(
                ConstraintSpec(
                    f"ev_charge_on_gate_{self._name}_t{t}",
                    charge_on[t] <= self._gate[t],
                )
            )
            if min_power > 0:
                constraints.append(
                    ConstraintSpec(
                        f"ev_charge_min_{self._name}_t{t}",
                        P_charge[t] >= min_power * charge_on[t],
                    )
                )
            constraints.append(
                ConstraintSpec(
                    f"ev_charge_max_{self._name}_t{t}",
                    P_charge[t] <= self._max_power_kw * charge_on[t],
                )
            )

        # Switch penalty (absolute on/off transitions), including t0 seeding from realtime state.
        if self._switch_penalty > 0:
            switch = self._switch(connection)
            threshold_kw = (
                self._min_power_kw
                if self._min_power_kw > 0
                else _EV_SWITCH_ON_THRESHOLD_KW
            )
            is_initial_on = bool(self._connected) and (self._realtime_power_kw >= threshold_kw)
            initial_on = 1 if is_initial_on else 0

            if 0 in connection.horizon.T:
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_up_{self._name}_t0",
                        switch[0] >= charge_on[0] - initial_on,
                    )
                )
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_down_{self._name}_t0",
                        switch[0] >= initial_on - charge_on[0],
                    )
                )
            for t in connection.horizon.T:
                if t == 0:
                    continue
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_up_{self._name}_t{t}",
                        switch[t] >= charge_on[t] - charge_on[t - 1],
                    )
                )
                constraints.append(
                    ConstraintSpec(
                        f"ev_switch_down_{self._name}_t{t}",
                        switch[t] >= charge_on[t - 1] - charge_on[t],
                    )
                )

        return constraints

    def objective(self, connection: ConnectionBinding) -> pulp.LpAffineExpression:
        if self._switch_penalty <= 0:
            return pulp.LpAffineExpression()
        switch = self._switch(connection)
        return self._switch_penalty * pulp.lpSum(switch.values())


@dataclass(frozen=True, slots=True)
class EvSoftDeadlineSpec:
    by_time: str
    deadline_at: datetime
    deadline_index: int
    target_soc_pct: float
    target_kwh: float
    shortfall_penalty: float


class EvSoftDeadlineFragment:
    """Soft EV SoC lower bounds enforced with non-negative shortfall slack."""

    def __init__(
        self,
        *,
        storages: tuple[StorageNode, ...],
        deadlines: tuple[EvSoftDeadlineSpec, ...],
        name: str,
    ) -> None:
        self._storages = storages
        self._deadlines = deadlines
        self._name = name
        self._shortfall_by_index: dict[int, pulp.LpVariable] = {
            idx: pulp.LpVariable(
                f"Ev_{self._name}_soft_deadline_shortfall_kwh_{idx}",
                lowBound=0,
            )
            for idx, _ in enumerate(self._deadlines)
        }

    @property
    def deadlines(self) -> tuple[EvSoftDeadlineSpec, ...]:
        return self._deadlines

    def shortfall_value(self, deadline_idx: int) -> float:
        return value_of(self._shortfall_by_index[deadline_idx])

    @property
    def constraints(self) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = []
        for idx, deadline in enumerate(self._deadlines):
            soc_at_deadline = pulp.lpSum(
                storage.E_by_i[deadline.deadline_index] for storage in self._storages
            )
            constraints.append(
                ConstraintSpec(
                    f"ev_soft_deadline_{self._name}_idx{idx}",
                    soc_at_deadline + self._shortfall_by_index[idx] >= deadline.target_kwh,
                )
            )
        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        if not self._deadlines:
            return pulp.LpAffineExpression()
        return pulp.lpSum(
            deadline.shortfall_penalty * self._shortfall_by_index[idx]
            for idx, deadline in enumerate(self._deadlines)
        )
