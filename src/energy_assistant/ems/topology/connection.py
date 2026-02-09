from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pulp

from energy_assistant.ems.milp.context import ConstraintSpec, ModelContext, ObjectiveTerm
from energy_assistant.ems.topology.link_components import EfficiencyModel, LinkComponentTemplate

if TYPE_CHECKING:
    from energy_assistant.ems.topology.link_components import LinkComponentModel

FlowDirection = Literal["a_to_b", "b_to_a"]


class ConnectionTemplate:
    def __init__(
        self,
        *,
        id: str,
        a_node_id: str,
        b_node_id: str,
        link_components: list[LinkComponentTemplate] | None = None,
    ) -> None:
        self.id = str(id)
        self.a_node_id = str(a_node_id)
        self.b_node_id = str(b_node_id)
        self.link_components = list(link_components or [])

    def bind(self, ctx: ModelContext) -> ConnectionModel:
        return ConnectionModel(ctx=ctx, template=self)


class ConnectionModel:
    def __init__(self, *, ctx: ModelContext, template: ConnectionTemplate) -> None:
        self.ctx = ctx
        self.template = template
        self.id = template.id
        self.a_node_id = template.a_node_id
        self.b_node_id = template.b_node_id

        T = ctx.horizon.T
        # Directional nonnegative flow variables for each timestep.
        self.P_a_to_b: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"P_{self.id}_a_to_b_kw",
            T,
            lowBound=0,
        )
        self.P_b_to_a: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"P_{self.id}_b_to_a_kw",
            T,
            lowBound=0,
        )

        # Bind LinkComponents sequentially so later components can depend on earlier ones.
        # (e.g. ExclusiveDirection relies on DirectionalLimit for its Big-M.)
        self._component_models: list[LinkComponentModel] = []
        for comp in template.link_components:
            self._component_models.append(comp.bind(ctx, self))

    @property
    def components(self) -> list[LinkComponentModel]:
        return list(self._component_models)

    def flow(self, direction: FlowDirection) -> dict[int, pulp.LpVariable]:
        if direction == "a_to_b":
            return self.P_a_to_b
        return self.P_b_to_a

    def efficiency(self, direction: FlowDirection) -> float:
        eta = 1.0
        for comp in self._component_models:
            if isinstance(comp, EfficiencyModel):
                eta *= comp.eta_a_to_b if direction == "a_to_b" else comp.eta_b_to_a
        return float(eta)

    @property
    def constraints(self) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = []
        for comp in self._component_models:
            constraints.extend(comp.constraints)
        return constraints

    @property
    def objective_terms(self) -> list[ObjectiveTerm]:
        terms: list[ObjectiveTerm] = []
        for comp in self._component_models:
            terms.extend(comp.objective_terms)
        return terms
