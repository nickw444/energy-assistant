from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

import pulp

from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.milp.context import ConstraintSpec
from energy_assistant.ems.topology.policies import ConnectionPolicy, TransferConnectionPolicy

P = TypeVar("P", bound=ConnectionPolicy)


def asset_has_transfer_policy(
    *,
    connection_id: str,
    policies: Mapping[str, ConnectionPolicy],
) -> None:
    transfer_policies = [
        policy for policy in policies.values() if isinstance(policy, TransferConnectionPolicy)
    ]
    if len(transfer_policies) != 1:
        raise ValueError(
            f"Connection {connection_id!r} requires exactly one TransferConnectionPolicy in "
            f"policies; got {len(transfer_policies)}"
        )


class Connection:
    """Bidirectional run-scoped connection between two nodes.

    For each direction we track source-side and sink-side power:
    - `power_in_ab`: power leaving node A toward node B (kW)
    - `power_out_ab`: power arriving at node B from node A (kW)
    - `power_in_ba`: power leaving node B toward node A (kW)
    - `power_out_ba`: power arriving at node A from node B (kW)

    Connection policies define constraints over these variables and are stored as a named map.

    Exactly one transfer-defining policy (`TransferConnectionPolicy`) must be present in
    `policies`. This keeps the physical transfer behavior explicit and unambiguous:
    each direction has one mapping from source-side flow to sink-side flow.
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
        asset_has_transfer_policy(connection_id=self.id, policies=self.policies)

        # Direction a->b: source=a, destination=b
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

        # Direction b->a: source=b, destination=a
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

    @property
    def flow_in_ab(self) -> dict[int, pulp.LpVariable]:
        return self.power_in_ab

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
        for policy in self.policies.values():
            constraints.extend(policy.constraints(self))
        return constraints

    @property
    def objective(self) -> pulp.LpAffineExpression:
        return pulp.lpSum(policy.objective(self) for policy in self.policies.values())
