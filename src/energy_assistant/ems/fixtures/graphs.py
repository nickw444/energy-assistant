from __future__ import annotations

import hashlib
import html
import re
import shutil
from dataclasses import dataclass
from typing import Literal

from graphviz import Digraph, Source

from energy_assistant.ems.components.battery import BatteryExportReservePolicy
from energy_assistant.ems.components.ev import EvChargeControl, EvSocIncentivesFragment
from energy_assistant.ems.components.pv import PvBinaryCurtailment, PvCurtailTracking
from energy_assistant.ems.system.system import EmsSystem
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.nodes import Node, StorageNode
from energy_assistant.ems.topology.nodes.node import NodeRole
from energy_assistant.ems.topology.policies import (
    ConnectionPolicy,
    DirectionalEfficiency,
    DirectionalLimit,
    FixedFlow,
    LinearCost,
    Passthrough,
    SoftDirectionalLimit,
    UpperBound,
)

GraphNodeKind = Literal[
    "switchboard",
    "grid",
    "load",
    "inverter",
    "pv",
    "battery",
    "ev",
    "fragment",
    "topology",
    "storage",
    "segment",
]

GraphEdgeKind = Literal["component", "fragment", "segment"]

_GRAPHVIZ_ENGINE = "neato"
_LOGICAL_NODE_COLORS: dict[GraphNodeKind, tuple[str, str]] = {
    "switchboard": ("#0f172a", "#dbeafe"),
    "grid": ("#0f766e", "#ccfbf1"),
    "load": ("#7c2d12", "#ffedd5"),
    "inverter": ("#1d4ed8", "#dbeafe"),
    "pv": ("#92400e", "#fef3c7"),
    "battery": ("#7c3aed", "#ede9fe"),
    "ev": ("#166534", "#dcfce7"),
    "fragment": ("#475569", "#f8fafc"),
    "topology": ("#334155", "#e2e8f0"),
    "storage": ("#4338ca", "#e0e7ff"),
    "segment": ("#475569", "#f8fafc"),
}
_TOPOLOGY_ROLE_COLORS: dict[NodeRole, tuple[str, str]] = {
    "bus": ("#0f172a", "#dbeafe"),
    "producer": ("#92400e", "#fef3c7"),
    "consumer": ("#7c2d12", "#ffedd5"),
    "prosumer": ("#166534", "#dcfce7"),
}


@dataclass(frozen=True, slots=True)
class GraphNodeSpec:
    id: str
    kind: GraphNodeKind
    title: str
    lines: tuple[str, ...]
    sort_key: tuple[str, ...]
    topology_role: NodeRole | None = None


@dataclass(frozen=True, slots=True)
class GraphEdgeSpec:
    source_id: str
    target_id: str
    kind: GraphEdgeKind
    sort_key: tuple[str, ...]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class GraphSpec:
    graph_id: str
    title: str
    nodes: tuple[GraphNodeSpec, ...]
    edges: tuple[GraphEdgeSpec, ...]


def build_logical_component_graph(system: EmsSystem) -> GraphSpec:
    nodes: list[GraphNodeSpec] = [
        _component_node(
            "switchboard",
            "Switchboard",
            lines=(
                "kind: switchboard",
                "id: switchboard",
                "bus: switchboard",
            ),
            node_kind="switchboard",
        ),
        _component_node(
            system.grid.id,
            "Grid",
            lines=(
                "kind: grid",
                f"id: {system.grid.id}",
                f"bus: {system.grid.bus_id}",
                f"export max: {system.grid.max_export_kw:.1f}kW",
            ),
            node_kind="grid",
        ),
        _component_node(
            system.base_load.id,
            system.base_load.name,
            lines=(
                "kind: load",
                f"id: {system.base_load.id}",
            ),
            node_kind="load",
        ),
    ]
    edges: list[GraphEdgeSpec] = [
        _graph_edge("switchboard", system.grid.id, kind="component"),
        _graph_edge("switchboard", system.base_load.id, kind="component"),
    ]

    for ev in sorted(system.evs.values(), key=lambda item: item.id):
        nodes.append(
            _component_node(
                ev.id,
                ev.name,
                lines=(
                    "kind: ev",
                    f"id: {ev.id}",
                    f"energy: {ev.capacity_kwh:.1f}kWh",
                    f"charge: {ev.min_power_kw:.1f}-{ev.max_power_kw:.1f}kW",
                    f"incentives: {len(ev.soc_incentives)}",
                ),
                node_kind="ev",
            )
        )
        edges.append(_graph_edge("switchboard", ev.id, kind="component"))
    for inverter in sorted(system.inverters.values(), key=lambda item: item.id):
        nodes.append(
            _component_node(
                inverter.id,
                inverter.name,
                lines=(
                    "kind: inverter",
                    f"id: {inverter.id}",
                    f"peak: {inverter.peak_power_kw:.1f}kW",
                    f"dc bus: {inverter.dc_bus_id}",
                ),
                node_kind="inverter",
            )
        )
        edges.append(_graph_edge("switchboard", inverter.id, kind="component"))

        for pv in sorted(inverter.pvs.values(), key=lambda item: item.id):
            nodes.append(
                _component_node(
                    pv.id,
                    pv.display_name,
                    lines=(
                        "kind: pv",
                        f"id: {pv.id}",
                        f"inverter: {pv.inverter_id}",
                        f"peak: {pv.peak_power_kw:.1f}kW",
                        f"curtailment: {pv.curtailment or 'none'}",
                    ),
                    node_kind="pv",
                )
            )
            edges.append(_graph_edge(inverter.id, pv.id, kind="component"))

        for battery in sorted(inverter.batteries.values(), key=lambda item: item.id):
            nodes.append(
                _component_node(
                    battery.id,
                    battery.name,
                    lines=(
                        "kind: battery",
                        f"id: {battery.id}",
                        f"inverter: {battery.inverter_id}",
                        f"capacity: {battery.capacity_kwh:.1f}kWh",
                        f"charge max: {battery.max_charge_kw:.1f}kW",
                        f"discharge max: {battery.max_discharge_kw:.1f}kW",
                    ),
                    node_kind="battery",
                )
            )
            edges.append(_graph_edge(inverter.id, battery.id, kind="component"))
    return GraphSpec(
        graph_id="logical_component_graph",
        title="Logical Component Graph",
        nodes=tuple(sorted(nodes, key=lambda item: item.sort_key)),
        edges=tuple(sorted(edges, key=lambda item: item.sort_key)),
    )


def build_topology_graph(graph: EnergyGraph) -> GraphSpec:
    nodes: list[GraphNodeSpec] = []
    edges: list[GraphEdgeSpec] = []

    for node in graph.nodes.values():
        nodes.append(_topology_node(node))

    for connection in graph.connections.values():
        if len(connection.ordered_policies) == 1:
            name, policy = connection.ordered_policies[0]
            edges.append(
                _graph_edge(
                    connection.a_node_id,
                    connection.b_node_id,
                    kind="segment",
                    label=_segment_edge_label(connection.id, name, policy),
                )
            )
            continue

        point_ids: list[str] = []
        for index in range(len(connection.ordered_policies) - 1):
            name, policy = connection.ordered_policies[index]
            point_id = f"segment:{connection.id}:{index:03d}:{name}"
            point_ids.append(point_id)
            nodes.append(
                GraphNodeSpec(
                    id=point_id,
                    kind="segment",
                    title="",
                    lines=(),
                    sort_key=("segment", connection.id, f"{index:03d}", name),
                )
            )

        segment_targets = [*point_ids, connection.b_node_id]
        previous_id = connection.a_node_id
        for (name, policy), target_id in zip(
            connection.ordered_policies,
            segment_targets,
            strict=True,
        ):
            edges.append(
                _graph_edge(
                    previous_id,
                    target_id,
                    kind="segment",
                    label=_segment_edge_label(connection.id, name, policy),
                )
            )
            previous_id = target_id

    for fragment in sorted(graph.extra_fragments, key=_fragment_sort_key):
        fragment_node = _topology_fragment_node(fragment)
        if fragment_node is None:
            continue
        nodes.append(fragment_node)
        edges.extend(_topology_fragment_edges(fragment, fragment_node.id))

    return GraphSpec(
        graph_id="energy_modeling_topology",
        title="Energy Modeling Topology",
        nodes=tuple(sorted(nodes, key=lambda item: item.sort_key)),
        edges=tuple(sorted(edges, key=lambda item: item.sort_key)),
    )


def render_graph_dot(graph: GraphSpec) -> str:
    dot = Digraph(name=graph.graph_id, engine=_GRAPHVIZ_ENGINE)
    dot_id_by_node_id = {node.id: _dot_id(node.id) for node in graph.nodes}
    dot.graph_attr.update(_graph_attributes(graph))
    dot.node_attr.update(fontname="Helvetica", fontsize="11")
    dot.edge_attr.update(
        color="#64748b",
        dir="none",
        fontname="Helvetica",
        fontsize="10",
        penwidth="1.4",
    )
    dot.attr(label=graph.title, labelloc="t", fontsize="22", fontname="Helvetica-Bold")

    for node in graph.nodes:
        if node.kind == "segment":
            dot.node(
                dot_id_by_node_id[node.id],
                label="",
                **_segment_node_attributes(),
            )
            continue
        if graph.graph_id == "logical_component_graph":
            dot.node(
                dot_id_by_node_id[node.id],
                label=_logical_node_label(node),
                **_logical_node_attributes(node),
            )
            continue
        dot.node(
            dot_id_by_node_id[node.id],
            label=_plain_multiline_label(node),
            **_topology_node_attributes(node),
        )

    for edge in graph.edges:
        attributes = _edge_attributes(edge)
        dot.edge(dot_id_by_node_id[edge.source_id], dot_id_by_node_id[edge.target_id], **attributes)

    return dot.source


def render_graph_svg(graph: GraphSpec) -> str:
    _ensure_graphviz_available()
    dot_source = render_graph_dot(graph)
    rendered = Source(dot_source, filename=graph.graph_id, engine=_GRAPHVIZ_ENGINE).pipe(
        format="svg",
        encoding="utf-8",
    )
    return _normalize_svg(rendered)


def compute_svg_hash(svg: str) -> str:
    return hashlib.sha256(svg.encode("utf-8")).hexdigest()[:16]


def _component_node(
    node_id: str,
    title: str,
    *,
    lines: tuple[str, ...],
    node_kind: GraphNodeKind,
) -> GraphNodeSpec:
    return GraphNodeSpec(
        id=node_id,
        kind=node_kind,
        title=title,
        lines=lines,
        sort_key=("component", node_kind, node_id),
    )


def _topology_node(node: Node) -> GraphNodeSpec:
    if isinstance(node, StorageNode):
        return GraphNodeSpec(
            id=node.id,
            kind="storage",
            title="storage node",
            lines=(
                f"role: {node.node_role}",
                f"id: {node.id}",
                f"capacity: {node.capacity_kwh:.1f}kWh",
                f"terminal: {node.terminal_mode}",
            ),
            sort_key=("topology", "storage", node.id),
            topology_role=node.node_role,
        )
    return GraphNodeSpec(
        id=node.id,
        kind="topology",
        title=f"{node.node_role} node",
        lines=(
            f"role: {node.node_role}",
            f"id: {node.id}",
        ),
        sort_key=("topology", node.node_role, node.id),
        topology_role=node.node_role,
    )


def _topology_fragment_node(
    fragment: object,
) -> GraphNodeSpec | None:
    if isinstance(fragment, BatteryExportReservePolicy):
        return GraphNodeSpec(
            id=f"fragment:{fragment.battery_node_id}:battery_reserve",
            kind="fragment",
            title="battery reserve",
            lines=(
                f"node: {fragment.battery_node_id}",
                f"reserve: {fragment.reserve_kwh:.1f}kWh",
            ),
            sort_key=("fragment", fragment.battery_node_id, "battery_reserve"),
        )
    if isinstance(fragment, EvSocIncentivesFragment):
        return GraphNodeSpec(
            id=f"fragment:{fragment.storage_node_id}:ev_incentives",
            kind="fragment",
            title="ev incentives",
            lines=(
                f"node: {fragment.storage_node_id}",
                f"incentives: {fragment.incentive_count}",
                f"bias: {fragment.grid_price_bias * 100:.0f}%",
            ),
            sort_key=("fragment", fragment.storage_node_id, "ev_incentives"),
        )
    return None


def _topology_fragment_edges(
    fragment: object,
    fragment_node_id: str,
) -> list[GraphEdgeSpec]:
    if isinstance(fragment, BatteryExportReservePolicy):
        return [
            _graph_edge(fragment.battery_node_id, fragment_node_id, kind="fragment"),
        ]
    if isinstance(fragment, EvSocIncentivesFragment):
        return [
            _graph_edge(fragment.storage_node_id, fragment_node_id, kind="fragment"),
        ]
    return []


def _fragment_sort_key(fragment: object) -> tuple[str, ...]:
    if isinstance(fragment, BatteryExportReservePolicy):
        return ("battery_reserve", fragment.battery_node_id)
    if isinstance(fragment, EvSocIncentivesFragment):
        return ("ev_incentives", fragment.storage_node_id)
    return (type(fragment).__name__,)


def _segment_title(name: str, policy: ConnectionPolicy) -> str:
    _ = name
    if isinstance(policy, Passthrough):
        return "passthrough"
    if isinstance(policy, DirectionalLimit):
        return "directional limit"
    if isinstance(policy, DirectionalEfficiency):
        return "efficiency"
    if isinstance(policy, FixedFlow):
        return "fixed flow"
    if isinstance(policy, UpperBound):
        return "upper bound"
    if isinstance(policy, LinearCost):
        return "linear cost"
    if isinstance(policy, SoftDirectionalLimit):
        return "soft limit"
    if isinstance(policy, EvChargeControl):
        return "charge control"
    if isinstance(policy, PvCurtailTracking):
        return "curtail tracking"
    if isinstance(policy, PvBinaryCurtailment):
        return "binary curtail"
    return type(policy).__name__.replace("_", " ").lower()


def _segment_logical_id(connection_id: str, segment_name: str) -> str:
    return f"{connection_id}.{segment_name}"


def _segment_edge_label(
    connection_id: str,
    segment_name: str,
    policy: ConnectionPolicy,
) -> str:
    logical_id = _segment_logical_id(connection_id, segment_name)
    kind = _segment_title(segment_name, policy)
    _ = policy
    return f"{logical_id}\n{kind}"
def _graph_edge(
    source_id: str,
    target_id: str,
    *,
    kind: GraphEdgeKind,
    label: str | None = None,
) -> GraphEdgeSpec:
    return GraphEdgeSpec(
        source_id=source_id,
        target_id=target_id,
        kind=kind,
        sort_key=(kind, source_id, target_id, label or ""),
        label=label,
    )


def _graph_attributes(graph: GraphSpec) -> dict[str, str]:
    base = {
        "bgcolor": "#f7fafb",
        "forcelabels": "true",
        "margin": "0.25",
        "outputorder": "edgesfirst",
        "overlap": "false",
        "pad": "0.35",
        "sep": "0.55",
        "splines": "true",
    }
    if graph.graph_id == "logical_component_graph":
        base["nodesep"] = "0.55"
    if graph.graph_id == "energy_modeling_topology":
        base["K"] = "5.4"
        base["esep"] = "+56"
        base["pad"] = "0.35"
        base["sep"] = "2.60"
    return base


def _logical_node_attributes(node: GraphNodeSpec) -> dict[str, str]:
    stroke, fill = _LOGICAL_NODE_COLORS[node.kind]
    return {
        "color": stroke,
        "fillcolor": fill,
        "margin": "0.0",
        "shape": "plain",
    }


def _topology_node_attributes(node: GraphNodeSpec) -> dict[str, str]:
    if node.kind == "fragment":
        stroke, fill = _LOGICAL_NODE_COLORS["fragment"]
        return {
            "color": stroke,
            "fillcolor": fill,
            "penwidth": "1.2",
            "shape": "box",
            "style": "rounded,filled,dashed",
        }
    if node.topology_role is None:
        stroke, fill = _LOGICAL_NODE_COLORS[node.kind]
    else:
        stroke, fill = _TOPOLOGY_ROLE_COLORS[node.topology_role]
    return {
        "color": stroke,
        "fillcolor": fill,
        "penwidth": "1.5",
        "shape": "ellipse" if node.kind == "topology" else "box",
        "style": "rounded,filled",
    }


def _segment_node_attributes() -> dict[str, str]:
    return {
        "color": "#475569",
        "fillcolor": "#475569",
        "fixedsize": "true",
        "fontcolor": "#475569",
        "fontsize": "8",
        "height": "0.12",
        "shape": "point",
        "style": "filled",
        "width": "0.12",
    }


def _logical_node_label(node: GraphNodeSpec) -> str:
    stroke, fill = _LOGICAL_NODE_COLORS[node.kind]
    title = html.escape(node.title)
    body_rows = "".join(
        (
            "<TR>"
            f'<TD ALIGN="LEFT" BGCOLOR="{fill}" BALIGN="LEFT">'
            f'<FONT FACE="Helvetica" POINT-SIZE="11">{html.escape(line)}</FONT>'
            "</TD>"
            "</TR>"
        )
        for line in node.lines
    )
    return (
        "<"
        '<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" '
        f'COLOR="{stroke}" BGCOLOR="{fill}">'
        "<TR>"
        f'<TD ALIGN="LEFT" BGCOLOR="{stroke}">'
        f'<FONT FACE="Helvetica-Bold" POINT-SIZE="12" COLOR="white">{title}</FONT>'
        "</TD>"
        "</TR>"
        f"{body_rows}"
        "</TABLE>"
        ">"
    )


def _plain_multiline_label(node: GraphNodeSpec) -> str:
    parts = [node.title, *node.lines]
    return "\n".join(parts)


def _edge_attributes(edge: GraphEdgeSpec) -> dict[str, str]:
    if edge.kind == "component":
        return {
            "color": "#64748b",
            "len": "1.8",
            "penwidth": "1.6",
        }
    if edge.kind == "fragment":
        return {
            "color": "#94a3b8",
            "len": "2.0",
            "penwidth": "1.2",
            "style": "dashed",
        }
    attributes = {
        "color": "#64748b",
        "fontcolor": "#334155",
        "fontsize": "9",
        "labelfloat": "false",
        "len": "7.8",
        "penwidth": "1.3",
    }
    if edge.label:
        attributes["label"] = edge.label
    return attributes


def _ensure_graphviz_available() -> None:
    if shutil.which(_GRAPHVIZ_ENGINE) is not None:
        return
    raise RuntimeError(
        "Graphviz is required to render EMS fixture graphs. "
        f"Install Graphviz so `{_GRAPHVIZ_ENGINE}` is available on PATH."
    )


def _normalize_svg(svg: str) -> str:
    normalized = re.sub(r"<!--.*?-->\s*", "", svg, flags=re.DOTALL)
    normalized = normalized.strip() + "\n"
    return normalized


def _dot_id(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_") or "node"
    return f"n_{slug}_{hashlib.sha1(value.encode('utf-8')).hexdigest()[:8]}"
