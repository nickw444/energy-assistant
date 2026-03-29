from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies import ConnectionPolicy, Passthrough

P = TypeVar("P", bound=ConnectionPolicy)


class _PolicyBinding:
    """Policy-scoped directional flow view within a connection."""

    def __init__(
        self,
        *,
        id: str,
        segment_key: str,
        horizon: Horizon,
        flow_in_ab: dict[int, pulp.LpVariable],
        flow_out_ab: dict[int, pulp.LpVariable],
        flow_in_ba: dict[int, pulp.LpVariable],
        flow_out_ba: dict[int, pulp.LpVariable],
    ) -> None:
        self.id = str(id)
        self.segment_key = str(segment_key)
        self.horizon = horizon
        self._flow_in_ab = flow_in_ab
        self._flow_out_ab = flow_out_ab
        self._flow_in_ba = flow_in_ba
        self._flow_out_ba = flow_out_ba

    @property
    def flow_in_ab(self) -> dict[int, pulp.LpVariable]:
        return self._flow_in_ab

    @property
    def flow_out_ab(self) -> dict[int, pulp.LpVariable]:
        return self._flow_out_ab

    @property
    def flow_in_ba(self) -> dict[int, pulp.LpVariable]:
        return self._flow_in_ba

    @property
    def flow_out_ba(self) -> dict[int, pulp.LpVariable]:
        return self._flow_out_ba


class Connection:
    """Bidirectional run-scoped connection between two nodes.

    For each direction we track source-side and sink-side power:
    - `power_in_ab`: power leaving node A toward node B (kW)
    - `power_out_ab`: power arriving at node B from node A (kW)
    - `power_in_ba`: power leaving node B toward node A (kW)
    - `power_out_ba`: power arriving at node A from node B (kW)

    Policies are composed as ordered segments. Each policy sees its own
    directional input/output variables, so multiple segment-like policies can
    be chained without special handling.
    """

    def __init__(
        self,
        *,
        horizon: Horizon,
        id: str,
        a_node_id: str,
        b_node_id: str,
        policies: Mapping[str, ConnectionPolicy] | None = None,
    ) -> None:
        self.horizon = horizon
        self.id = str(id)
        self.a_node_id = str(a_node_id)
        self.b_node_id = str(b_node_id)
        self.policies: dict[str, ConnectionPolicy] = dict(policies or {})

        self.power_in_ab: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"P_{self.id}_in_ab_kw",
            self.horizon.T,
            lowBound=0,
        )
        self.power_out_ab: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"P_{self.id}_out_ab_kw",
            self.horizon.T,
            lowBound=0,
        )
        self.power_in_ba: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"P_{self.id}_in_ba_kw",
            self.horizon.T,
            lowBound=0,
        )
        self.power_out_ba: dict[int, pulp.LpVariable] = pulp.LpVariable.dicts(
            f"P_{self.id}_out_ba_kw",
            self.horizon.T,
            lowBound=0,
        )

        self._ordered_policies = self._build_ordered_policies()
        self._policy_bindings = self._build_policy_bindings()

    @property
    def flow_in_ab(self) -> dict[int, pulp.LpVariable]:
        return self.power_in_ab

    @property
    def segment_key(self) -> str:
        return self.id

    @property
    def flow_out_ab(self) -> dict[int, pulp.LpVariable]:
        return self.power_out_ab

    @property
    def flow_in_ba(self) -> dict[int, pulp.LpVariable]:
        return self.power_in_ba

    @property
    def flow_out_ba(self) -> dict[int, pulp.LpVariable]:
        return self.power_out_ba

    def flow_out_of_node(self, node_id: str) -> dict[int, pulp.LpVariable]:
        nid = str(node_id)
        if nid == self.a_node_id:
            return self.flow_in_ab
        if nid == self.b_node_id:
            return self.flow_in_ba
        raise ValueError(f"Node {node_id!r} is not connected to {self.id!r}")

    def flow_into_node(self, node_id: str) -> dict[int, pulp.LpVariable]:
        nid = str(node_id)
        if nid == self.a_node_id:
            return self.flow_out_ba
        if nid == self.b_node_id:
            return self.flow_out_ab
        raise ValueError(f"Node {node_id!r} is not connected to {self.id!r}")

    def _build_ordered_policies(self) -> list[tuple[str, ConnectionPolicy]]:
        if not self.policies:
            return [("passthrough", Passthrough())]
        return list(self.policies.items())

    def _build_policy_bindings(self) -> list[_PolicyBinding]:
        policy_count = len(self._ordered_policies)
        ab_points: list[dict[int, pulp.LpVariable]] = [self.power_in_ab]
        ba_points: list[dict[int, pulp.LpVariable]] = [self.power_in_ba]

        # Intermediate segment boundaries let policy-defined flow laws compose.
        for idx in range(1, policy_count):
            ab_points.append(
                pulp.LpVariable.dicts(
                    f"P_{self.id}_seg{idx}_ab_kw",
                    self.horizon.T,
                    lowBound=0,
                )
            )
            ba_points.append(
                pulp.LpVariable.dicts(
                    f"P_{self.id}_seg{idx}_ba_kw",
                    self.horizon.T,
                    lowBound=0,
                )
            )

        ab_points.append(self.power_out_ab)
        ba_points.append(self.power_out_ba)

        bindings: list[_PolicyBinding] = []
        for idx, (name, _policy) in enumerate(self._ordered_policies):
            bindings.append(
                _PolicyBinding(
                    id=self.id,
                    segment_key=f"{self.id}_{name}_seg{idx}",
                    horizon=self.horizon,
                    flow_in_ab=ab_points[idx],
                    flow_out_ab=ab_points[idx + 1],
                    flow_in_ba=ba_points[idx],
                    flow_out_ba=ba_points[idx + 1],
                )
            )
        return bindings

    def policy(self, name: str, policy_type: type[P]) -> P:
        policy = self.policies.get(str(name))
        if policy is None:
            raise KeyError(f"Connection {self.id!r} has no policy named {name!r}")
        if not isinstance(policy, policy_type):
            raise TypeError(
                f"Connection {self.id!r} policy {name!r} is {type(policy).__name__}, "
                f"expected {policy_type.__name__}"
            )
        return policy

    def find_policy(self, name: str, policy_type: type[P]) -> P | None:
        policy = self.policies.get(str(name))
        if policy is None:
            return None
        if not isinstance(policy, policy_type):
            raise TypeError(
                f"Connection {self.id!r} policy {name!r} is {type(policy).__name__}, "
                f"expected {policy_type.__name__}"
            )
        return policy

    @property
    def constraints(self) -> list[ConstraintSpec]:
        constraints: list[ConstraintSpec] = []
        for (_name, policy), binding in zip(
            self._ordered_policies,
            self._policy_bindings,
            strict=True,
        ):
            constraints.extend(policy.constraints(binding))
        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.lpSum(
            policy.objective(binding)
            for (_name, policy), binding in zip(
                self._ordered_policies,
                self._policy_bindings,
                strict=True,
            )
        )
