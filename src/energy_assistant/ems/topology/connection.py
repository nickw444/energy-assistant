from __future__ import annotations

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintDescriptor
from energy_assistant.ems.topology.link_components import FlowDirection, LinkComponent


class Connection:
    """Bidirectional connection between two nodes.

    This is a persistent topology object that is re-used across planning runs. Per-run PuLP vars
    and constraint expressions are (re)created when `set_horizon(...)` is called.
    """

    def __init__(
        self,
        *,
        id: str,
        a_node_id: str,
        b_node_id: str,
        link_components: list[LinkComponent] | None = None,
    ) -> None:
        self.id = str(id)
        self.a_node_id = str(a_node_id)
        self.b_node_id = str(b_node_id)
        self.link_components = list(link_components or [])

        self._horizon: Horizon | None = None
        self.T: list[int] = []
        self.dt_hours: dict[int, float] = {}

        self._var_cache: dict[str, dict[int, pulp.LpVariable]] = {}

        # Directional nonnegative flow variables for each timestep.
        self.P_a_to_b: dict[int, pulp.LpVariable] = {}
        self.P_b_to_a: dict[int, pulp.LpVariable] = {}

    def set_horizon(self, horizon: Horizon) -> None:
        """(Re)create per-run variables for the given horizon."""
        self._horizon = horizon
        self.T[:] = list(horizon.T)
        self.dt_hours = {t: float(horizon.dt_hours(t)) for t in self.T}

        # Reset per-run caches.
        self._var_cache = {}

        self.P_a_to_b = pulp.LpVariable.dicts(
            f"P_{self.id}_a_to_b_kw",
            self.T,
            lowBound=0,
        )
        self.P_b_to_a = pulp.LpVariable.dicts(
            f"P_{self.id}_b_to_a_kw",
            self.T,
            lowBound=0,
        )

    def _ensure_horizon(self) -> None:
        if self._horizon is None:
            raise ValueError(f"Connection {self.id!r} has no active horizon; call set_horizon()")

    @property
    def components(self) -> list[LinkComponent]:
        return list(self.link_components)

    def flow(self, direction: FlowDirection) -> dict[int, pulp.LpVariable]:
        self._ensure_horizon()
        if direction == "a_to_b":
            return self.P_a_to_b
        return self.P_b_to_a

    def transport_efficiency(self, direction: FlowDirection) -> float:
        eta = 1.0
        for comp in self.link_components:
            eta *= float(comp.transport_efficiency(direction))
        return float(eta)

    def storage_efficiency(self, direction: FlowDirection) -> float:
        eta = 1.0
        for comp in self.link_components:
            eta *= float(comp.storage_efficiency(direction))
        return float(eta)

    def binary_series(self, name: str) -> dict[int, pulp.LpVariable]:
        self._ensure_horizon()
        key = f"bin:{name}"
        if key not in self._var_cache:
            self._var_cache[key] = pulp.LpVariable.dicts(
                str(name),
                self.T,
                lowBound=0,
                upBound=1,
                cat="Binary",
            )
        return self._var_cache[key]

    def nonnegative_series(self, name: str) -> dict[int, pulp.LpVariable]:
        self._ensure_horizon()
        key = f"nn:{name}"
        if key not in self._var_cache:
            self._var_cache[key] = pulp.LpVariable.dicts(
                str(name),
                self.T,
                lowBound=0,
            )
        return self._var_cache[key]

    def unit_series(self, name: str) -> dict[int, pulp.LpVariable]:
        """Continuous per-slot variable in [0,1]."""
        self._ensure_horizon()
        key = f"unit:{name}"
        if key not in self._var_cache:
            self._var_cache[key] = pulp.LpVariable.dicts(
                str(name),
                self.T,
                lowBound=0,
                upBound=1,
            )
        return self._var_cache[key]

    @property
    def constraints(self) -> list[ConstraintDescriptor]:
        constraints: list[ConstraintDescriptor] = []
        self._ensure_horizon()
        for comp in self.link_components:
            constraints.extend(comp.constraints(self))
        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        self._ensure_horizon()
        return pulp.lpSum(comp.objective(self) for comp in self.link_components)
