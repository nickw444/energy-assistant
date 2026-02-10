from __future__ import annotations

from typing import Literal, Protocol

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.deferred import Deferred, DeferredSeries
from energy_assistant.ems.topology.link_components import FlowDirection

NodeDomain = Literal["ac", "dc"]
TerminalMode = Literal["none", "hard", "adaptive"]


class GraphLike(Protocol):
    def connections_for_node(self, node_id: str) -> list[Connection]: ...


class Node:
    """Topology node (persistent across planning runs)."""

    def __init__(self, *, id: str, name: str) -> None:
        self.id = str(id)
        self.name = str(name)
        self._horizon: Horizon | None = None

    def set_horizon(self, horizon: Horizon, graph: GraphLike) -> None:
        # Default nodes do nothing; subclasses allocate per-run vars/constraints.
        _ = graph
        self._horizon = horizon

    @property
    def constraints(self) -> list[ConstraintDescriptor]:
        return []

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.LpAffineExpression()


class BusNode(Node):
    """Junction node enforcing energy conservation via per-slot balance constraints."""

    def __init__(self, *, id: str, name: str, domain: NodeDomain | None = None) -> None:
        super().__init__(id=str(id), name=str(name))
        self.domain = domain
        self._constraints: list[ConstraintDescriptor] = []

    def set_horizon(self, horizon: Horizon, graph: GraphLike) -> None:
        self._horizon = horizon
        constraints: list[ConstraintDescriptor] = []
        for t in horizon.T:
            incoming: pulp.LpAffineExpression = pulp.LpAffineExpression()
            outgoing: pulp.LpAffineExpression = pulp.LpAffineExpression()
            for conn in graph.connections_for_node(self.id):
                if conn.a_node_id == self.id:
                    incoming += conn.P_b_to_a[t] * conn.transport_efficiency("b_to_a")
                    outgoing += conn.P_a_to_b[t]
                elif conn.b_node_id == self.id:
                    incoming += conn.P_a_to_b[t] * conn.transport_efficiency("a_to_b")
                    outgoing += conn.P_b_to_a[t]
                else:
                    raise ValueError("Graph adjacency invariant violated")
            constraints.append(
                ConstraintDescriptor(
                    f"balance_{self.id}_t{t}",
                    incoming - outgoing == 0,
                )
            )
        self._constraints = constraints

    @property
    def constraints(self) -> list[ConstraintDescriptor]:
        return list(self._constraints)


class PortNode(Node):
    """Terminal node used for attaching external sources/sinks (no intrinsic constraints)."""

    pass


class StorageNode(Node):
    """Energy storage node with SoC dynamics and optional terminal constraints/objective."""

    def __init__(
        self,
        *,
        id: str,
        name: str,
        capacity_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        initial_soc_kwh: Deferred[float],
        terminal_mode: TerminalMode = "none",
        terminal_reserve_kwh: float = 0.0,
        terminal_penalty_per_kwh: float | Literal["mean", "median"] | None = "median",
        price_import_raw: DeferredSeries[float] | None = None,
        terminal_soc_value_per_kwh: float | None = None,
    ) -> None:
        super().__init__(id=str(id), name=str(name))
        self.capacity_kwh = float(capacity_kwh)
        self.soc_min_kwh = float(soc_min_kwh)
        self.soc_max_kwh = float(soc_max_kwh)
        self.initial_soc_kwh = initial_soc_kwh
        self.terminal_mode: TerminalMode = terminal_mode
        self.terminal_reserve_kwh = float(terminal_reserve_kwh)
        self.terminal_penalty_per_kwh: float | Literal["mean", "median"] | None = (
            terminal_penalty_per_kwh
        )
        self.price_import_raw = price_import_raw
        self.terminal_soc_value_per_kwh = (
            None if terminal_soc_value_per_kwh is None else float(terminal_soc_value_per_kwh)
        )

        self.E_by_i: dict[int, pulp.LpVariable] = {}
        self.P_charge_kw: dict[int, pulp.LpVariable] = {}
        self.P_discharge_kw: dict[int, pulp.LpVariable] = {}
        self.terminal_shortfall_kwh: pulp.LpVariable | None = None

        self._connection: Connection | None = None
        self._constraints: list[ConstraintDescriptor] = []
        self._objective: pulp.LpAffineExpression = pulp.LpAffineExpression()

    def set_horizon(self, horizon: Horizon, graph: GraphLike) -> None:
        self._horizon = horizon
        initial_soc_kwh = float(self.initial_soc_kwh.get())

        soc_indices = range(int(horizon.num_intervals) + 1)
        self.E_by_i = pulp.LpVariable.dicts(
            f"E_{self.id}_kwh",
            soc_indices,
            lowBound=self.soc_min_kwh,
            upBound=self.soc_max_kwh,
        )

        incident = graph.connections_for_node(self.id)
        if len(incident) != 1:
            raise ValueError(
                f"Storage node {self.id!r} must have exactly 1 incident connection; "
                f"got {len(incident)}"
            )
        conn = incident[0]
        self._connection = conn

        # Determine charge/discharge directional flows relative to this storage node.
        if conn.a_node_id == self.id:
            charge_flow = conn.P_b_to_a  # other -> storage
            discharge_flow = conn.P_a_to_b  # storage -> other
            charge_dir: FlowDirection = "b_to_a"
            discharge_dir: FlowDirection = "a_to_b"
        elif conn.b_node_id == self.id:
            charge_flow = conn.P_a_to_b
            discharge_flow = conn.P_b_to_a
            charge_dir = "a_to_b"
            discharge_dir = "b_to_a"
        else:
            raise ValueError("Graph adjacency invariant violated")

        self.P_charge_kw = charge_flow
        self.P_discharge_kw = discharge_flow

        eta_charge = float(conn.storage_efficiency(charge_dir))
        eta_discharge = float(conn.storage_efficiency(discharge_dir))
        if eta_charge <= 0 or eta_charge > 1.0:
            raise ValueError(f"charge efficiency must be in (0,1]; got {eta_charge}")
        if eta_discharge <= 0 or eta_discharge > 1.0:
            raise ValueError(f"discharge efficiency must be in (0,1]; got {eta_discharge}")

        constraints: list[ConstraintDescriptor] = [
            ConstraintDescriptor(
                f"soc_initial_{self.id}",
                self.E_by_i[0] == float(initial_soc_kwh),
            )
        ]
        for t in horizon.T:
            constraints.append(
                ConstraintDescriptor(
                    f"soc_step_{self.id}_t{t}",
                    self.E_by_i[t + 1]
                    == self.E_by_i[t]
                    + (self.P_charge_kw[t] * eta_charge - self.P_discharge_kw[t] / eta_discharge)
                    * float(horizon.dt_hours(t)),
                )
            )

        self.terminal_shortfall_kwh = None
        objective_parts: list[pulp.LpAffineExpression] = []

        terminal_idx = int(horizon.num_intervals)
        if self.terminal_mode == "hard":
            constraints.append(
                ConstraintDescriptor(
                    f"soc_terminal_{self.id}",
                    self.E_by_i[terminal_idx] >= float(initial_soc_kwh),
                )
            )
        elif self.terminal_mode == "adaptive":
            if self.price_import_raw is None:
                raise ValueError(
                    f"Storage node {self.id!r} terminal_mode='adaptive' requires price_import_raw"
                )
            ratio = _terminal_soc_return_ratio(horizon)
            floor_kwh = min(float(initial_soc_kwh), float(self.terminal_reserve_kwh))
            target_kwh = float(floor_kwh + ratio * (float(initial_soc_kwh) - floor_kwh))

            self.terminal_shortfall_kwh = pulp.LpVariable(
                f"E_{self.id}_terminal_shortfall_kwh",
                lowBound=0,
            )
            constraints.append(
                ConstraintDescriptor(
                    f"soc_terminal_{self.id}",
                    self.E_by_i[terminal_idx] + self.terminal_shortfall_kwh >= target_kwh,
                )
            )

            price_import_raw = self.price_import_raw.get_for_horizon(horizon)
            penalty = _terminal_penalty_per_kwh(
                horizon=horizon,
                price_import=price_import_raw,
                penalty_cfg=self.terminal_penalty_per_kwh,
                ratio=ratio,
            )
            if penalty > 0:
                objective_parts.append(float(penalty) * self.terminal_shortfall_kwh)

        if self.terminal_soc_value_per_kwh is not None and self.terminal_soc_value_per_kwh > 0:
            objective_parts.append(
                -float(self.terminal_soc_value_per_kwh) * self.E_by_i[terminal_idx]
            )

        self._constraints = constraints
        self._objective = (
            pulp.lpSum(objective_parts) if objective_parts else pulp.LpAffineExpression()
        )

    @property
    def constraints(self) -> list[ConstraintDescriptor]:
        return list(self._constraints)

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return self._objective


_TERMINAL_SOC_REFERENCE_MINUTES = 1440.0


def _horizon_duration_minutes(horizon: Horizon) -> float:
    if not horizon.slots:
        return 0.0
    return (horizon.slots[-1].end - horizon.start).total_seconds() / 60.0


def _terminal_soc_return_ratio(horizon: Horizon) -> float:
    # Keep parity with legacy builder: ratio = min(horizon, ref) / max(horizon, ref)
    # so that 24h keeps full strength and both shorter/longer relax toward reserve.
    horizon_minutes = _horizon_duration_minutes(horizon)
    if horizon_minutes <= 0:
        return 1.0
    reference_minutes = float(_TERMINAL_SOC_REFERENCE_MINUTES)
    shorter = min(horizon_minutes, reference_minutes)
    longer = max(horizon_minutes, reference_minutes)
    return float(shorter / longer) if longer > 0 else 1.0


def _average(values: list[float]) -> float:
    return 0.0 if not values else float(sum(values) / len(values))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(float(x) for x in values)
    mid = len(vals) // 2
    if len(vals) % 2 == 1:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def _terminal_penalty_per_kwh(
    *,
    horizon: Horizon,
    price_import: list[float],
    penalty_cfg: float | Literal["mean", "median"] | None,
    ratio: float,
) -> float:
    penalty: float
    if penalty_cfg is None or penalty_cfg == "median":
        penalty = _median(price_import)
    elif penalty_cfg == "mean":
        penalty = _average(price_import)
    else:
        penalty = float(penalty_cfg)
    penalty = max(0.0, float(penalty))
    penalty *= float(ratio)
    return float(penalty)
