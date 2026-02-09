from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext, ObjectiveTerm

if TYPE_CHECKING:
    from energy_assistant.ems.topology.graph import EnergyGraphModel


NodeDomain = Literal["ac", "dc"]


class BusNodeTemplate:
    def __init__(self, *, id: str, name: str, domain: NodeDomain | None = None) -> None:
        self.id = str(id)
        self.name = str(name)
        self.domain = domain

    def bind(self, graph: EnergyGraphModel) -> BusNodeModel:
        return BusNodeModel(ctx=graph.ctx, graph=graph, template=self)


class PortNodeTemplate:
    def __init__(self, *, id: str, name: str) -> None:
        self.id = str(id)
        self.name = str(name)

    def bind(self, graph: EnergyGraphModel) -> PortNodeModel:
        return PortNodeModel(ctx=graph.ctx, template=self)


class StorageNodeTemplate:
    def __init__(
        self,
        *,
        id: str,
        name: str,
        capacity_kwh: float,
        soc_min_kwh: float,
        soc_max_kwh: float,
        storage_efficiency: float,
        initial_soc_kwh_key: str,
        terminal_mode: Literal["none", "hard", "adaptive"] = "none",
        terminal_reserve_kwh: float = 0.0,
        terminal_penalty_per_kwh: float | Literal["mean", "median"] | None = "median",
        price_import_raw_key: str = "price_import_raw",
        terminal_soc_value_per_kwh: float | None = None,
        mode: Literal["bidirectional", "charge_only"] = "bidirectional",
    ) -> None:
        self.id = str(id)
        self.name = str(name)
        self.capacity_kwh = float(capacity_kwh)
        self.soc_min_kwh = float(soc_min_kwh)
        self.soc_max_kwh = float(soc_max_kwh)
        self.storage_efficiency = float(storage_efficiency)
        self.initial_soc_kwh_key = str(initial_soc_kwh_key)
        self.terminal_mode: Literal["none", "hard", "adaptive"] = terminal_mode
        self.terminal_reserve_kwh = float(terminal_reserve_kwh)
        self.terminal_penalty_per_kwh: float | Literal["mean", "median"] | None = (
            terminal_penalty_per_kwh
        )
        self.price_import_raw_key = str(price_import_raw_key)
        self.terminal_soc_value_per_kwh = (
            None if terminal_soc_value_per_kwh is None else float(terminal_soc_value_per_kwh)
        )
        self.mode: Literal["bidirectional", "charge_only"] = mode

    def bind(self, graph: EnergyGraphModel) -> StorageNodeModel:
        return StorageNodeModel(ctx=graph.ctx, graph=graph, template=self)


class NodeModel:
    @property
    def constraints(self) -> list[ConstraintSpec]:
        return []

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return []


class BusNodeModel(NodeModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        graph: EnergyGraphModel,
        template: BusNodeTemplate,
    ) -> None:
        self.ctx = ctx
        self.graph = graph
        self.id = template.id
        self.name = template.name
        self.domain = template.domain

        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            incoming: pulp.LpAffineExpression = pulp.LpAffineExpression()
            outgoing: pulp.LpAffineExpression = pulp.LpAffineExpression()
            for conn in graph.connections_for_node(self.id):
                if conn.a_node_id == self.id:
                    incoming += conn.P_b_to_a[t] * conn.efficiency("b_to_a")
                    outgoing += conn.P_a_to_b[t]
                elif conn.b_node_id == self.id:
                    incoming += conn.P_a_to_b[t] * conn.efficiency("a_to_b")
                    outgoing += conn.P_b_to_a[t]
                else:
                    raise ValueError("Graph adjacency invariant violated")
            self._constraints.append(
                ConstraintSpec(
                    f"balance_{self.id}_t{t}",
                    incoming - outgoing == 0,
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)


class PortNodeModel(NodeModel):
    def __init__(self, *, ctx: ModelContext, template: PortNodeTemplate) -> None:
        self.ctx = ctx
        self.id = template.id
        self.name = template.name


class StorageNodeModel(NodeModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        graph: EnergyGraphModel,
        template: StorageNodeTemplate,
    ) -> None:
        self.ctx = ctx
        self.graph = graph
        self.id = template.id
        self.name = template.name
        self.capacity_kwh = float(template.capacity_kwh)
        self.soc_min_kwh = float(template.soc_min_kwh)
        self.soc_max_kwh = float(template.soc_max_kwh)
        self.storage_efficiency = float(template.storage_efficiency)
        self.mode = template.mode
        self.terminal_mode = template.terminal_mode
        self.terminal_reserve_kwh = float(template.terminal_reserve_kwh)
        self.terminal_penalty_per_kwh = template.terminal_penalty_per_kwh
        self.price_import_raw_key = template.price_import_raw_key
        self.terminal_soc_value_per_kwh = template.terminal_soc_value_per_kwh

        initial_soc_kwh = float(ctx.inputs.float(template.initial_soc_kwh_key))

        soc_indices = range(ctx.horizon.num_intervals + 1)
        self.E_by_i: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
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
        self._connection = incident[0]

        # Determine charge/discharge directional flows relative to this storage node.
        if self._connection.a_node_id == self.id:
            charge_flow = self._connection.P_b_to_a  # other -> storage
            discharge_flow = self._connection.P_a_to_b  # storage -> other
        elif self._connection.b_node_id == self.id:
            charge_flow = self._connection.P_a_to_b
            discharge_flow = self._connection.P_b_to_a
        else:
            raise ValueError("Graph adjacency invariant violated")

        self.P_charge_kw = charge_flow
        self.P_discharge_kw = discharge_flow

        eta = float(self.storage_efficiency)
        if eta <= 0 or eta > 1.0:
            raise ValueError(f"storage_efficiency must be in (0,1]; got {eta}")

        self._constraints: list[ConstraintSpec] = []
        self._constraints.append(
            ConstraintSpec(
                f"soc_initial_{self.id}",
                self.E_by_i[0] == float(initial_soc_kwh),
            )
        )
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"soc_step_{self.id}_t{t}",
                    self.E_by_i[t + 1]
                    == self.E_by_i[t]
                    + (self.P_charge_kw[t] * eta - self.P_discharge_kw[t] / eta)
                    * ctx.horizon.dt_hours(t),
                )
            )

        self.terminal_shortfall_kwh: pulp.LpVariable | None = None
        self._objective_terms: list[ObjectiveTerm] = []

        terminal_idx = int(ctx.horizon.num_intervals)
        if self.terminal_mode == "hard":
            self._constraints.append(
                ConstraintSpec(
                    f"soc_terminal_{self.id}",
                    self.E_by_i[terminal_idx] >= float(initial_soc_kwh),
                )
            )
        elif self.terminal_mode == "adaptive":
            ratio = _terminal_soc_return_ratio(ctx.horizon)
            floor_kwh = min(float(initial_soc_kwh), float(self.terminal_reserve_kwh))
            target_kwh = float(floor_kwh + ratio * (float(initial_soc_kwh) - floor_kwh))
            self.terminal_shortfall_kwh = pulp.LpVariable(
                f"E_{self.id}_terminal_shortfall_kwh",
                lowBound=0,
            )
            self._constraints.append(
                ConstraintSpec(
                    f"soc_terminal_{self.id}",
                    self.E_by_i[terminal_idx] + self.terminal_shortfall_kwh >= target_kwh,
                )
            )
            penalty = _terminal_penalty_per_kwh(
                horizon=ctx.horizon,
                price_import=ctx.inputs.float_series(self.price_import_raw_key),
                penalty_cfg=self.terminal_penalty_per_kwh,
                ratio=ratio,
            )
            if penalty > 0:
                self._objective_terms.append(
                    ObjectiveTerm(
                        penalty * self.terminal_shortfall_kwh,
                        name=f"terminal_soc:{self.id}",
                    )
                )

        if self.terminal_soc_value_per_kwh is not None and self.terminal_soc_value_per_kwh > 0:
            self._objective_terms.append(
                ObjectiveTerm(
                    -float(self.terminal_soc_value_per_kwh) * self.E_by_i[terminal_idx],
                    name=f"soc_value:{self.id}",
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)


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
