from __future__ import annotations

from pathlib import Path
from typing import Any

import graphviz

from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.nodes import Node, StorageNode
from energy_assistant.ems.topology.policies import (
    DirectionalEfficiency,
    DirectionalLimit,
    FixedFlow,
    LinearCost,
    SoftDirectionalLimit,
    UpperBound,
)
from energy_assistant.models.config import AppConfig
from energy_assistant.models.plant import SwitchboardComponentConfig

_LOGICAL_COMPONENT_FILL_BY_TYPE = {
    "switchboard": "#eff6ff",
    "grid": "#f1f5f9",
    "load": "#fdf2f8",
    "inverter": "#f5f3ff",
    "pv": "#fefce8",
    "battery": "#ecfdf5",
    "load_controlled_ev": "#f0fdfa",
}

_LOGICAL_COMPONENT_BORDER_BY_TYPE = {
    "switchboard": "#2563eb",
    "grid": "#475569",
    "load": "#db2777",
    "inverter": "#7c3aed",
    "pv": "#ca8a04",
    "battery": "#059669",
    "load_controlled_ev": "#0f766e",
}


def write_logical_component_graph_svg(app_config: AppConfig, output: Path) -> None:
    """Render the configured logical plant component graph as SVG."""
    dot = _base_digraph("ems_logical_component_graph")
    dot.attr("node", shape="box", style="rounded,filled")
    dot.attr("edge", color="#64748b")

    for component_id, component in app_config.plant.items():
        dot.node(
            _logical_node_id(component_id),
            label=f"{component_id}\\n{component.type}",
            fillcolor=_logical_component_fill(component.type),
            color=_logical_component_border(component.type),
            tooltip=f"{component_id} ({component.type})",
        )

    for component_id, component in app_config.plant.items():
        if isinstance(component, SwitchboardComponentConfig):
            continue
        dot.edge(
            _logical_node_id(component_id),
            _logical_node_id(component.connection),
            tooltip=f"{component_id} -> {component.connection}",
        )

    _write_svg(dot, output)


def write_topological_energy_graph_svg(graph: EnergyGraph, output: Path) -> None:
    """Render the solve-scoped physical energy topology graph as SVG."""
    dot = _base_digraph("ems_topological_energy_graph")
    dot.attr("node", fontname="Helvetica")
    dot.attr("edge", color="#64748b", arrowsize="0.7")

    for node in graph.nodes:
        dot.node(
            _topology_node_id(node),
            label=_node_label(node),
            shape="box",
            style="rounded,filled",
            fillcolor=_node_fill(node),
            tooltip=f"{node.id} ({node.node_role})",
        )

    for connection in graph.connections:
        _add_connection_policy_edges(dot, connection)

    if graph.extra_fragments:
        cluster = graphviz.Digraph(name="cluster_fragments")
        cluster.attr(label="Fragments", color="#cbd5e1", style="rounded")
        for index, fragment in enumerate(graph.extra_fragments):
            fragment_id = f"fragment_{index}"
            cluster.node(
                fragment_id,
                label=type(fragment).__name__,
                shape="note",
                style="filled",
                fillcolor="#fff7ed",
            )
        dot.subgraph(cluster)

    _write_svg(dot, output)


def _base_digraph(name: str) -> graphviz.Digraph:
    dot = graphviz.Digraph(name=name, graph_attr={"rankdir": "LR"})
    dot.attr(
        "graph",
        bgcolor="white",
        pad="0.25",
        nodesep="0.6",
        ranksep="0.9",
        outputorder="edgesfirst",
    )
    dot.attr("node", fontname="Helvetica", fontsize="11", margin="0.08,0.05")
    dot.attr("edge", fontname="Helvetica", fontsize="9")
    return dot


def _write_svg(dot: graphviz.Digraph, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    svg = dot.pipe(format="svg")
    output.write_bytes(svg)


def _logical_node_id(component_id: str) -> str:
    return f"component_{component_id}"


def _logical_component_fill(component_type: str) -> str:
    return _LOGICAL_COMPONENT_FILL_BY_TYPE.get(component_type, "#f8fafc")


def _logical_component_border(component_type: str) -> str:
    return _LOGICAL_COMPONENT_BORDER_BY_TYPE.get(component_type, "#94a3b8")


def _topology_node_id(node: Node) -> str:
    return _topology_node_id_from_value(node.id)


def _topology_node_id_from_value(node_id: str) -> str:
    return f"node_{node_id}"


def _node_fill(node: Node) -> str:
    if isinstance(node, StorageNode):
        return "#ecfdf5"
    if node.node_role == "bus":
        return "#eff6ff"
    if node.node_role == "producer":
        return "#fefce8"
    if node.node_role == "consumer":
        return "#fdf2f8"
    return "#f8fafc"


def _node_label(node: Node) -> str:
    lines = [str(node.id), node.name, node.node_role]
    if isinstance(node, StorageNode):
        lines.append(
            f"{_fmt_number(node.initial_soc_kwh)} / {_fmt_number(node.capacity_kwh)} kWh"
        )
    return "\\n".join(lines)


def _add_connection_policy_edges(dot: graphviz.Digraph, connection: Connection) -> None:
    policies = list(connection.policies.items())
    if not policies:
        policies = [("passthrough", None)]

    chain_nodes = _connection_chain_nodes(connection, len(policies))
    for node_id in chain_nodes[1:-1]:
        dot.node(node_id, label="", shape="point", width="0.04", height="0.04")

    for index, (policy_name, policy) in enumerate(policies):
        dot.edge(
            chain_nodes[index],
            chain_nodes[index + 1],
            label=_compact_policy_label(policy_name, policy),
            tooltip=_policy_edge_tooltip(connection.id, policy_name, policy),
        )


def _connection_chain_nodes(connection: Connection, policy_count: int) -> list[str]:
    endpoints = [
        _topology_node_id_from_value(connection.a_node_id),
        _topology_node_id_from_value(connection.b_node_id),
    ]
    if policy_count <= 1:
        return endpoints
    intermediate = [
        f"connection_{connection.id}_junction_{index}" for index in range(1, policy_count)
    ]
    return [endpoints[0], *intermediate, endpoints[1]]


def _policy_edge_tooltip(connection_id: str, name: str, policy: Any | None) -> str:
    if policy is None:
        return f"{connection_id}: {name}"
    return f"{connection_id}: {_policy_label(name, policy)}"


def _compact_policy_label(name: str, policy: Any | None) -> str:
    if policy is None:
        return "pass"
    policy_header = f"{name}\\n{type(policy).__name__}"
    if _is_dynamic_series_policy(policy):
        return policy_header
    if isinstance(policy, DirectionalLimit):
        label = "limit ex" if policy.exclusive else "limit"
        return (
            f"{policy_header}\\n"
            f"{label}\\n"
            f"ab<={_fmt_limit_short(policy.max_a_to_b_kw)}\\n"
            f"ba<={_fmt_limit_short(policy.max_b_to_a_kw)}"
        )
    if isinstance(policy, DirectionalEfficiency):
        return (
            f"{policy_header}\\n"
            f"eff\\n"
            f"ab={_fmt_number(policy.eta_a_to_b)}\\n"
            f"ba={_fmt_number(policy.eta_b_to_a)}"
        )
    if isinstance(policy, FixedFlow):
        return (
            f"{policy_header}\\n"
            f"fixed\\n"
            f"{_direction_arrow(policy.direction)}\\n"
            f"kw={_fmt_series(policy.values_kw)}"
        )
    if isinstance(policy, UpperBound):
        return (
            f"{policy_header}\\n"
            f"upper bound\\n"
            f"{_direction_arrow(policy.direction)}\\n"
            f"<={_fmt_series(policy.upper_bounds_kw)}"
        )
    if isinstance(policy, SoftDirectionalLimit):
        return (
            f"{policy_header}\\n"
            f"soft\\n"
            f"{_direction_arrow(policy.direction)}\\n"
            f"<={_fmt_series(policy.limit_kw)}\\n"
            f"pen={_fmt_number(policy.penalty_per_kwh)}"
        )
    if isinstance(policy, LinearCost):
        return (
            f"{policy_header}\\n"
            f"cost\\n"
            f"ab={_fmt_series(policy.cost_a_to_b_per_kwh)}\\n"
            f"ba={_fmt_series(policy.cost_b_to_a_per_kwh)}"
        )
    return policy_header


def _policy_label(name: str, policy: Any) -> str:
    if _is_dynamic_series_policy(policy):
        return f"{name}: dynamic per-timestep policy"
    if isinstance(policy, DirectionalLimit):
        exclusive = ", exclusive" if policy.exclusive else ""
        return (
            f"{name}: limit a->b={_fmt_limit(policy.max_a_to_b_kw)}, "
            f"b->a={_fmt_limit(policy.max_b_to_a_kw)}{exclusive}"
        )
    if isinstance(policy, DirectionalEfficiency):
        return (
            f"{name}: efficiency a->b={_fmt_number(policy.eta_a_to_b)}, "
            f"b->a={_fmt_number(policy.eta_b_to_a)}"
        )
    if isinstance(policy, FixedFlow):
        return f"{name}: fixed {policy.direction}, kw={_fmt_series(policy.values_kw)}"
    if isinstance(policy, UpperBound):
        return (
            f"{name}: upper bound {policy.direction}, "
            f"kw<={_fmt_series(policy.upper_bounds_kw)}"
        )
    if isinstance(policy, SoftDirectionalLimit):
        return (
            f"{name}: soft limit {policy.direction}, "
            f"kw<={_fmt_series(policy.limit_kw)}, "
            f"penalty={_fmt_number(policy.penalty_per_kwh)}"
        )
    if isinstance(policy, LinearCost):
        return (
            f"{name}: linear cost, "
            f"a->b={_fmt_series(policy.cost_a_to_b_per_kwh)}, "
            f"b->a={_fmt_series(policy.cost_b_to_a_per_kwh)}"
        )
    return f"{name}: {type(policy).__name__}"


def _is_dynamic_series_policy(policy: Any) -> bool:
    return isinstance(policy, FixedFlow | UpperBound | SoftDirectionalLimit | LinearCost)


def _fmt_limit(value: float | None) -> str:
    return "unbounded" if value is None else _fmt_number(value)


def _fmt_limit_short(value: float | None) -> str:
    return "inf" if value is None else _fmt_number(value)


def _fmt_number(value: float) -> str:
    # Force fixed-point output so tiny values stay readable in rendered SVG labels.
    formatted = f"{value:.8f}".rstrip("0").rstrip(".")
    if formatted in {"", "-0"}:
        return "0"
    return formatted


def _fmt_series(values: list[float]) -> str:
    if not values:
        return "none"
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        return _fmt_number(minimum)
    return f"{_fmt_number(minimum)}..{_fmt_number(maximum)}"


def _direction_arrow(direction: str) -> str:
    if direction == "a_to_b":
        return "a->b"
    if direction == "b_to_a":
        return "b->a"
    return direction
