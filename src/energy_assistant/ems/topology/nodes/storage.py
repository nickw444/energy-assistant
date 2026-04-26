from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes.node import Node

TerminalMode = Literal["none", "hard"]


@dataclass(frozen=True, slots=True)
class FixedTerminalSocValue:
    value_per_kwh: float


@dataclass(frozen=True, slots=True)
class ForecastPercentileTerminalSocValue:
    percentile: float = 70.0
    lookahead_window_minutes: int = 1440
    price_floor_per_kwh: float = 0.0


type TerminalSocValueConfig = FixedTerminalSocValue | ForecastPercentileTerminalSocValue


class StorageNode(Node):
    """Energy storage node with SoC dynamics and optional terminal constraints/objective."""

    def __init__(
        self,
        *,
        horizon: Horizon,
        id: NodeId,
        name: str,
        capacity_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        initial_soc_kwh: float,
        terminal_mode: TerminalMode = "none",
        price_import_raw: list[float] | None = None,
        terminal_soc_value: TerminalSocValueConfig | None = None,
    ) -> None:
        super().__init__(
            horizon=horizon,
            id=id,
            name=name,
            node_role="prosumer",
        )
        self.capacity_kwh = capacity_kwh
        self.soc_min_kwh = soc_min_kwh
        self.soc_max_kwh = soc_max_kwh
        self.initial_soc_kwh = initial_soc_kwh
        self.terminal_mode: TerminalMode = terminal_mode
        self.price_import_raw = None if price_import_raw is None else list(price_import_raw)
        self.terminal_soc_value = terminal_soc_value

        soc_indices = range(int(horizon.num_intervals) + 1)
        self.E_by_i: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"E_{self.id}_kwh",
            soc_indices,
            lowBound=self.soc_min_kwh,
            upBound=self.soc_max_kwh,
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

    def _terminal_constraint(self) -> ConstraintSpec | None:
        if self.terminal_mode == "hard":
            return ConstraintSpec(
                f"soc_terminal_{self.id}",
                self.terminal_soc >= self.initial_soc_kwh,
            )

        return None

    def _price_import_raw(self) -> list[float]:
        if self.price_import_raw is None:
            raise ValueError(
                f"Storage node {self.id!r} forecast-derived terminal value requires price_import_raw"
            )
        if len(self.price_import_raw) != int(self.horizon.num_intervals):
            raise ValueError(
                f"Storage node {self.id!r} price_import_raw length "
                f"{len(self.price_import_raw)} "
                f"!= num_intervals={int(self.horizon.num_intervals)}"
            )
        return self.price_import_raw

    def _terminal_soc_value_per_kwh(self) -> float | None:
        if self.terminal_soc_value is None:
            return None

        if isinstance(self.terminal_soc_value, FixedTerminalSocValue):
            value_per_kwh = self.terminal_soc_value.value_per_kwh
        else:
            price_import_raw = self._price_import_raw()
            price_window = _tail_price_window(
                horizon=self.horizon,
                price_import=price_import_raw,
                window_minutes=self.terminal_soc_value.lookahead_window_minutes,
            )
            value_per_kwh = max(
                self.terminal_soc_value.price_floor_per_kwh,
                _percentile(price_window, self.terminal_soc_value.percentile),
            )

        return value_per_kwh if value_per_kwh > 0 else None

    def _terminal_soc_value_objective(self) -> pulp.LpAffineExpression | None:
        value_per_kwh = self._terminal_soc_value_per_kwh()
        if value_per_kwh is None:
            return None
        return -value_per_kwh * self.terminal_soc

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
        terminal_value = self._terminal_soc_value_objective()
        if terminal_value is not None:
            objective_parts.append(terminal_value)

        return pulp.lpSum(objective_parts) if objective_parts else pulp.LpAffineExpression()

    def bind_terminal_import_prices(self, price_import: list[float]) -> None:
        """Set grid import prices for forecast-derived terminal energy value."""

        self.price_import_raw = list(price_import)


def _tail_price_window(
    *,
    horizon: Horizon,
    price_import: list[float],
    window_minutes: int,
) -> list[float]:
    if not price_import:
        return []

    selected: list[float] = []
    remaining_minutes = float(window_minutes)
    for t in reversed(horizon.T):
        selected.append(price_import[t])
        remaining_minutes -= horizon.dt_hours(t) * 60.0
        if remaining_minutes <= 0:
            break

    return list(reversed(selected)) if selected else list(price_import)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0

    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    if percentile <= 0:
        return vals[0]
    if percentile >= 100:
        return vals[-1]

    rank = (len(vals) - 1) * (percentile / 100.0)
    lower_idx = int(rank)
    upper_idx = lower_idx if lower_idx == len(vals) - 1 else lower_idx + 1
    lower = vals[lower_idx]
    upper = vals[upper_idx]
    fraction = rank - lower_idx
    return lower + (upper - lower) * fraction
