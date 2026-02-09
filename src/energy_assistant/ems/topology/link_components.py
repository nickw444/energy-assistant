from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext, ObjectiveTerm

if TYPE_CHECKING:
    from energy_assistant.ems.topology.connection import ConnectionModel

FlowDirection = Literal["a_to_b", "b_to_a"]


class LinkComponentTemplate:
    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> LinkComponentModel:
        raise NotImplementedError


class LinkComponentModel:
    @property
    def constraints(self) -> list[ConstraintSpec]:
        return []

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return []


class DirectionalLimit(LinkComponentTemplate):
    """Hard directional limit on connection flows (kW)."""

    def __init__(self, *, max_a_to_b_kw: float, max_b_to_a_kw: float) -> None:
        self.max_a_to_b_kw = float(max_a_to_b_kw)
        self.max_b_to_a_kw = float(max_b_to_a_kw)

    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> DirectionalLimitModel:
        return DirectionalLimitModel(
            ctx=ctx,
            connection=connection,
            max_a_to_b_kw=self.max_a_to_b_kw,
            max_b_to_a_kw=self.max_b_to_a_kw,
        )


class DirectionalLimitModel(LinkComponentModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        connection: ConnectionModel,
        max_a_to_b_kw: float,
        max_b_to_a_kw: float,
    ) -> None:
        self._ctx = ctx
        self._connection = connection
        self.max_a_to_b_kw = float(max_a_to_b_kw)
        self.max_b_to_a_kw = float(max_b_to_a_kw)

        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"limit_{connection.id}_a_to_b_t{t}",
                    connection.P_a_to_b[t] <= self.max_a_to_b_kw,
                )
            )
            self._constraints.append(
                ConstraintSpec(
                    f"limit_{connection.id}_b_to_a_t{t}",
                    connection.P_b_to_a[t] <= self.max_b_to_a_kw,
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)


class ExclusiveDirection(LinkComponentTemplate):
    """Prevent simultaneous bidirectional flow using a per-slot binary selector."""

    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> ExclusiveDirectionModel:
        # Determine Big-M values from the connection's DirectionalLimit component.
        max_a_to_b = _find_directional_limit_max(connection, "a_to_b")
        max_b_to_a = _find_directional_limit_max(connection, "b_to_a")
        return ExclusiveDirectionModel(
            ctx=ctx,
            connection=connection,
            max_a_to_b_kw=max_a_to_b,
            max_b_to_a_kw=max_b_to_a,
        )


class ExclusiveDirectionModel(LinkComponentModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        connection: ConnectionModel,
        max_a_to_b_kw: float,
        max_b_to_a_kw: float,
    ) -> None:
        self._ctx = ctx
        self._connection = connection
        self.max_a_to_b_kw = float(max_a_to_b_kw)
        self.max_b_to_a_kw = float(max_b_to_a_kw)

        self.dir_select: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"Dir_{connection.id}",
            ctx.horizon.T,
            lowBound=0,
            upBound=1,
            cat="Binary",
        )
        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"exclusive_{connection.id}_a_to_b_t{t}",
                    connection.P_a_to_b[t] <= self.max_a_to_b_kw * self.dir_select[t],
                )
            )
            self._constraints.append(
                ConstraintSpec(
                    f"exclusive_{connection.id}_b_to_a_t{t}",
                    connection.P_b_to_a[t]
                    <= self.max_b_to_a_kw * (1 - self.dir_select[t]),
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)


class Efficiency(LinkComponentTemplate):
    """Directional transport efficiency applied by the receiving Bus balance."""

    def __init__(self, *, eta_a_to_b: float, eta_b_to_a: float) -> None:
        self.eta_a_to_b = float(eta_a_to_b)
        self.eta_b_to_a = float(eta_b_to_a)

    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> EfficiencyModel:
        _ = ctx
        _ = connection
        return EfficiencyModel(eta_a_to_b=self.eta_a_to_b, eta_b_to_a=self.eta_b_to_a)


class EfficiencyModel(LinkComponentModel):
    def __init__(self, *, eta_a_to_b: float, eta_b_to_a: float) -> None:
        self.eta_a_to_b = float(eta_a_to_b)
        self.eta_b_to_a = float(eta_b_to_a)


class LinearCostSeries(LinkComponentTemplate):
    """Linear objective cost for directional flows ($/kWh), with per-slot series coefficients."""

    def __init__(self, *, cost_a_to_b_key: str, cost_b_to_a_key: str, name: str) -> None:
        self.cost_a_to_b_key = str(cost_a_to_b_key)
        self.cost_b_to_a_key = str(cost_b_to_a_key)
        self.name = str(name)

    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> LinearCostSeriesModel:
        return LinearCostSeriesModel(
            ctx=ctx,
            connection=connection,
            cost_a_to_b=ctx.inputs.float_series(self.cost_a_to_b_key),
            cost_b_to_a=ctx.inputs.float_series(self.cost_b_to_a_key),
            name=self.name,
        )


class LinearCostSeriesModel(LinkComponentModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        connection: ConnectionModel,
        cost_a_to_b: list[float],
        cost_b_to_a: list[float],
        name: str,
    ) -> None:
        self._ctx = ctx
        self._connection = connection
        self._name = str(name)
        self.cost_a_to_b = [float(x) for x in cost_a_to_b]
        self.cost_b_to_a = [float(x) for x in cost_b_to_a]

        expr = pulp.lpSum(
            (
                connection.P_a_to_b[t] * float(self.cost_a_to_b[t])
                + connection.P_b_to_a[t] * float(self.cost_b_to_a[t])
            )
            * ctx.horizon.dt_hours(t)
            for t in ctx.horizon.T
        )
        self._objective_terms = [ObjectiveTerm(expr, name=f"cost:{self._name}:{connection.id}")]

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)


class SoftDirectionalLimitSeries(LinkComponentTemplate):
    """Soft upper limit (kW) on one direction with slack + penalty."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        limit_key: str,
        penalty_per_kwh: float,
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.limit_key = str(limit_key)
        self.penalty_per_kwh = float(penalty_per_kwh)
        self.name = str(name)

    def bind(
        self,
        ctx: ModelContext,
        connection: ConnectionModel,
    ) -> SoftDirectionalLimitSeriesModel:
        return SoftDirectionalLimitSeriesModel(
            ctx=ctx,
            connection=connection,
            direction=self.direction,
            limit_series=ctx.inputs.float_series(self.limit_key),
            penalty_per_kwh=self.penalty_per_kwh,
            name=self.name,
        )


class SoftDirectionalLimitSeriesModel(LinkComponentModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        connection: ConnectionModel,
        direction: FlowDirection,
        limit_series: list[float],
        penalty_per_kwh: float,
        name: str,
    ) -> None:
        self._ctx = ctx
        self._connection = connection
        self.direction: FlowDirection = direction
        self.limit_series = [float(x) for x in limit_series]
        self.penalty_per_kwh = float(penalty_per_kwh)
        self.name = str(name)

        self.slack_kw: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"S_{connection.id}_{direction}_kw",
            ctx.horizon.T,
            lowBound=0,
        )

        flow = connection.P_a_to_b if direction == "a_to_b" else connection.P_b_to_a

        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"soft_limit_{name}_{connection.id}_{direction}_t{t}",
                    flow[t] <= float(self.limit_series[t]) + self.slack_kw[t],
                )
            )

        penalty_expr = pulp.lpSum(
            float(self.penalty_per_kwh) * self.slack_kw[t] * ctx.horizon.dt_hours(t)
            for t in ctx.horizon.T
        )
        self._objective_terms = [
            ObjectiveTerm(
                penalty_expr,
                name=f"penalty:{name}:{connection.id}",
            )
        ]

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        return list(self._objective_terms)


class FixedFlowSeries(LinkComponentTemplate):
    """Fix one directional flow to a per-slot series (kW)."""

    def __init__(self, *, direction: FlowDirection, value_key: str, name: str) -> None:
        self.direction: FlowDirection = direction
        self.value_key = str(value_key)
        self.name = str(name)

    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> FixedFlowSeriesModel:
        return FixedFlowSeriesModel(
            ctx=ctx,
            connection=connection,
            direction=self.direction,
            values=ctx.inputs.float_series(self.value_key),
            name=self.name,
        )


class FixedFlowSeriesModel(LinkComponentModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        connection: ConnectionModel,
        direction: FlowDirection,
        values: list[float],
        name: str,
    ) -> None:
        self._ctx = ctx
        self._connection = connection
        self.direction: FlowDirection = direction
        self.values = [float(x) for x in values]
        self.name = str(name)

        flow = connection.P_a_to_b if direction == "a_to_b" else connection.P_b_to_a
        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"fixed_flow_{name}_{connection.id}_{direction}_t{t}",
                    flow[t] == float(self.values[t]),
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)


class UpperBoundSeries(LinkComponentTemplate):
    """Per-slot upper bound on one directional flow (kW)."""

    def __init__(self, *, direction: FlowDirection, ub_key: str, name: str) -> None:
        self.direction: FlowDirection = direction
        self.ub_key = str(ub_key)
        self.name = str(name)

    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> UpperBoundSeriesModel:
        return UpperBoundSeriesModel(
            ctx=ctx,
            connection=connection,
            direction=self.direction,
            upper_bounds=ctx.inputs.float_series(self.ub_key),
            name=self.name,
        )


class UpperBoundSeriesModel(LinkComponentModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        connection: ConnectionModel,
        direction: FlowDirection,
        upper_bounds: list[float],
        name: str,
    ) -> None:
        self._ctx = ctx
        self._connection = connection
        self.direction: FlowDirection = direction
        self.upper_bounds = [float(x) for x in upper_bounds]
        self.name = str(name)

        flow = connection.P_a_to_b if direction == "a_to_b" else connection.P_b_to_a
        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"ub_{name}_{connection.id}_{direction}_t{t}",
                    flow[t] <= float(self.upper_bounds[t]),
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)


class GateSeries(LinkComponentTemplate):
    """Gate one directional flow by a per-slot [0,1] series with a fixed max power."""

    def __init__(
        self,
        *,
        direction: FlowDirection,
        gate_key: str,
        max_kw: float,
        name: str,
    ) -> None:
        self.direction: FlowDirection = direction
        self.gate_key = str(gate_key)
        self.max_kw = float(max_kw)
        self.name = str(name)

    def bind(self, ctx: ModelContext, connection: ConnectionModel) -> GateSeriesModel:
        return GateSeriesModel(
            ctx=ctx,
            connection=connection,
            direction=self.direction,
            gate=ctx.inputs.float_series(self.gate_key),
            max_kw=self.max_kw,
            name=self.name,
        )


class GateSeriesModel(LinkComponentModel):
    def __init__(
        self,
        *,
        ctx: ModelContext,
        connection: ConnectionModel,
        direction: FlowDirection,
        gate: list[float],
        max_kw: float,
        name: str,
    ) -> None:
        self._ctx = ctx
        self._connection = connection
        self.direction: FlowDirection = direction
        self.gate = [float(x) for x in gate]
        self.max_kw = float(max_kw)
        self.name = str(name)

        flow = connection.P_a_to_b if direction == "a_to_b" else connection.P_b_to_a
        self._constraints: list[ConstraintSpec] = []
        for t in ctx.horizon.T:
            self._constraints.append(
                ConstraintSpec(
                    f"gate_{name}_{connection.id}_{direction}_t{t}",
                    flow[t] <= self.max_kw * float(self.gate[t]),
                )
            )

    @property
    def constraints(self) -> list[ConstraintSpec]:
        return list(self._constraints)


def _find_directional_limit_max(connection: ConnectionModel, direction: FlowDirection) -> float:
    # ExclusiveDirection requires an explicit DirectionalLimit so Big-M is well-defined.
    max_a_to_b: float | None = None
    max_b_to_a: float | None = None
    for comp in connection.components:
        if isinstance(comp, DirectionalLimitModel):
            if max_a_to_b is not None or max_b_to_a is not None:
                raise ValueError(
                    f"Multiple DirectionalLimit components on connection {connection.id}"
                )
            max_a_to_b = comp.max_a_to_b_kw
            max_b_to_a = comp.max_b_to_a_kw
    if max_a_to_b is None or max_b_to_a is None:
        raise ValueError(
            f"ExclusiveDirection requires DirectionalLimit on connection {connection.id}"
        )
    return max_a_to_b if direction == "a_to_b" else max_b_to_a
