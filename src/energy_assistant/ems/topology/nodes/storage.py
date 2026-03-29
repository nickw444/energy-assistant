from __future__ import annotations

from typing import Literal

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.nodes.node import Node

TerminalMode = Literal["none", "hard", "adaptive"]


class StorageNode(Node):
    """Energy storage node with SoC dynamics and optional terminal constraints/objective."""

    def __init__(
        self,
        *,
        horizon: Horizon,
        id: str,
        name: str,
        capacity_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        initial_soc_kwh: float,
        terminal_mode: TerminalMode = "none",
        terminal_reserve_kwh: float = 0.0,
        terminal_penalty_per_kwh: float | Literal["mean", "median"] | None = "median",
        price_import_raw: list[float] | None = None,
        terminal_soc_value_per_kwh: float | None = None,
    ) -> None:
        super().__init__(
            horizon=horizon,
            id=str(id),
            name=str(name),
            node_role="prosumer",
        )
        self.capacity_kwh = float(capacity_kwh)
        self.soc_min_kwh = float(soc_min_kwh)
        self.soc_max_kwh = float(soc_max_kwh)
        self.initial_soc_kwh = float(initial_soc_kwh)
        self.terminal_mode: TerminalMode = terminal_mode
        self.terminal_reserve_kwh = float(terminal_reserve_kwh)
        self.terminal_penalty_per_kwh: float | Literal["mean", "median"] | None = (
            terminal_penalty_per_kwh
        )
        self.price_import_raw = (
            None if price_import_raw is None else [float(v) for v in price_import_raw]
        )
        self.terminal_soc_value_per_kwh = (
            None if terminal_soc_value_per_kwh is None else float(terminal_soc_value_per_kwh)
        )

        soc_indices = range(int(horizon.num_intervals) + 1)
        self.E_by_i: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"E_{self.id}_kwh",
            soc_indices,
            lowBound=self.soc_min_kwh,
            upBound=self.soc_max_kwh,
        )
        self.terminal_shortfall_kwh: pulp.LpVariable | None = (
            pulp.LpVariable(f"E_{self.id}_terminal_shortfall_kwh", lowBound=0)
            if self.terminal_mode == "adaptive"
            else None
        )

    @property
    def _storage_connection(self) -> Connection:
        incident = self.connections
        if len(incident) != 1:
            raise ValueError(
                f"Storage node {self.id!r} must have exactly 1 incident connection; "
                f"got {len(incident)}"
            )
        conn = incident[0]

        if self.id not in {conn.a_node_id, conn.b_node_id}:
            raise ValueError("Graph adjacency invariant violated")
        return conn

    @property
    def charge_power(self) -> dict[int, pulp.LpVariable]:
        return self._storage_connection.flow_into_node(self.id)

    @property
    def discharge_power(self) -> dict[int, pulp.LpVariable]:
        return self._storage_connection.flow_out_of_node(self.id)

    @property
    def net_storage_power(self) -> dict[int, pulp.LpAffineExpression]:
        charge = self.charge_power
        discharge = self.discharge_power
        return {
            t: charge[t] - discharge[t]
            for t in self.horizon.T
        }

    @property
    def terminal_index(self) -> int:
        return int(self.horizon.num_intervals)

    @property
    def terminal_soc(self) -> pulp.LpVariable:
        return self.E_by_i[self.terminal_index]

    def _adaptive_shortfall_var(self) -> pulp.LpVariable:
        if self.terminal_shortfall_kwh is None:
            raise ValueError(f"Storage node {self.id!r} missing terminal shortfall variable")
        return self.terminal_shortfall_kwh

    def _adaptive_terminal_target_kwh(self) -> float:
        ratio = _terminal_soc_return_ratio(self.horizon)
        floor_kwh = min(self.initial_soc_kwh, self.terminal_reserve_kwh)
        return float(floor_kwh + ratio * (self.initial_soc_kwh - floor_kwh))

    def _terminal_constraint(self) -> ConstraintSpec | None:
        if self.terminal_mode == "hard":
            return ConstraintSpec(
                f"soc_terminal_{self.id}",
                self.terminal_soc >= self.initial_soc_kwh,
            )

        if self.terminal_mode == "adaptive":
            if self.price_import_raw is None:
                raise ValueError(
                    f"Storage node {self.id!r} terminal_mode='adaptive' requires price_import_raw"
                )
            return ConstraintSpec(
                f"soc_terminal_{self.id}",
                self.terminal_soc + self._adaptive_shortfall_var()
                >= self._adaptive_terminal_target_kwh(),
            )

        return None

    def _adaptive_terminal_penalty_objective(self) -> pulp.LpAffineExpression | None:
        if self.terminal_mode != "adaptive":
            return None
        if self.price_import_raw is None:
            raise ValueError(
                f"Storage node {self.id!r} terminal_mode='adaptive' requires price_import_raw"
            )
        if len(self.price_import_raw) != int(self.horizon.num_intervals):
            raise ValueError(
                f"Storage node {self.id!r} price_import_raw length "
                f"{len(self.price_import_raw)} "
                f"!= num_intervals={int(self.horizon.num_intervals)}"
            )

        penalty = _terminal_penalty_per_kwh(
            horizon=self.horizon,
            price_import=list(self.price_import_raw),
            penalty_cfg=self.terminal_penalty_per_kwh,
            ratio=_terminal_soc_return_ratio(self.horizon),
        )
        if penalty <= 0:
            return None
        return float(penalty) * self._adaptive_shortfall_var()

    def _terminal_soc_value_objective(self) -> pulp.LpAffineExpression | None:
        if self.terminal_soc_value_per_kwh is None or self.terminal_soc_value_per_kwh <= 0:
            return None
        return -float(self.terminal_soc_value_per_kwh) * self.terminal_soc

    @property
    def constraints(self) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = [
            ConstraintSpec(
                f"soc_initial_{self.id}",
                self.E_by_i[0] == self.initial_soc_kwh,
            )
        ]
        net_power = self.net_storage_power
        constraints.extend(
            ConstraintSpec(
                f"soc_step_{self.id}_t{t}",
                self.E_by_i[t + 1]
                == self.E_by_i[t] + net_power[t] * self.horizon.dt_hours(t),
            )
            for t in self.horizon.T
        )

        terminal_constraint = self._terminal_constraint()
        if terminal_constraint is not None:
            constraints.append(terminal_constraint)

        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        objective_parts: list[pulp.LpAffineExpression] = []

        adaptive_penalty = self._adaptive_terminal_penalty_objective()
        if adaptive_penalty is not None:
            objective_parts.append(adaptive_penalty)

        terminal_value = self._terminal_soc_value_objective()
        if terminal_value is not None:
            objective_parts.append(terminal_value)

        return pulp.lpSum(objective_parts) if objective_parts else pulp.LpAffineExpression()


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
