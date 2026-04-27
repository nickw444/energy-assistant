from __future__ import annotations

from dataclasses import dataclass
from html import escape
from math import ceil, sqrt
from pathlib import Path

from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.models.config import AppConfig
from energy_assistant.models.plant import (
    SwitchboardComponentConfig,
)


@dataclass(frozen=True, slots=True)
class _GraphNode:
    id: str
    label: str
    subtitle: str


@dataclass(frozen=True, slots=True)
class _GraphEdge:
    source: str
    target: str
    label: str


def write_logical_component_graph_svg(app_config: AppConfig, output_path: Path) -> None:
    nodes: list[_GraphNode] = []
    edges: list[_GraphEdge] = []

    for component_id in sorted(app_config.plant):
        component = app_config.plant[component_id]
        nodes.append(
            _GraphNode(
                id=component_id,
                label=component_id,
                subtitle=component.type,
            )
        )
        if isinstance(component, SwitchboardComponentConfig):
            continue
        edges.append(
            _GraphEdge(
                source=component_id,
                target=component.connection,
                label=component.type,
            )
        )

    output_path.write_text(_render_graph_svg(nodes=nodes, edges=edges), encoding="utf-8")


def write_topology_graph_svg(snapshot: ModelSnapshot, output_path: Path) -> None:
    graph = snapshot.graph
    nodes = [
        _GraphNode(
            id=str(node.id),
            label=str(node.id),
            subtitle=node.node_role,
        )
        for node in graph.nodes
    ]
    edges = [
        _GraphEdge(
            source=str(connection.a_node_id),
            target=str(connection.b_node_id),
            label=_format_policy_label(connection.id, list(connection.policies)),
        )
        for connection in graph.connections
    ]
    output_path.write_text(_render_graph_svg(nodes=nodes, edges=edges), encoding="utf-8")


def _format_policy_label(connection_id: str, policies: list[str]) -> str:
    policy_label = ", ".join(policies) if policies else "passthrough"
    return f"{connection_id} [{policy_label}]"


def _render_graph_svg(*, nodes: list[_GraphNode], edges: list[_GraphEdge]) -> str:
    if not nodes:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="140">'
            '<text x="24" y="80" font-size="16" font-family="monospace">No nodes</text>'
            "</svg>\n"
        )

    node_width = 180
    node_height = 60
    padding = 32
    columns = max(1, ceil(sqrt(len(nodes))))
    rows = ceil(len(nodes) / columns)
    canvas_width = padding * 2 + columns * node_width
    canvas_height = padding * 2 + rows * node_height + 80

    positions: dict[str, tuple[float, float]] = {}
    for index, node in enumerate(nodes):
        row = index // columns
        col = index % columns
        x = padding + (col + 0.5) * node_width
        y = padding + (row + 0.5) * node_height
        positions[node.id] = (x, y)

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width}" '
            f'height="{canvas_height}" viewBox="0 0 {canvas_width} {canvas_height}">'
        ),
        "<style>",
        "text { font-family: monospace; fill: #1f2937; }",
        ".edge { stroke: #64748b; stroke-width: 1.5; }",
        ".edge-label { font-size: 11px; fill: #334155; }",
        ".node { fill: #eef2ff; stroke: #6366f1; stroke-width: 1.2; rx: 8; ry: 8; }",
        ".node-title { font-size: 12px; font-weight: 700; }",
        ".node-subtitle { font-size: 11px; fill: #475569; }",
        "</style>",
    ]

    for edge in edges:
        source = positions.get(edge.source)
        target = positions.get(edge.target)
        if source is None or target is None:
            continue
        sx, sy = source
        tx, ty = target
        mx = (sx + tx) / 2
        my = (sy + ty) / 2 - 4
        lines.append(
            f'<line class="edge" x1="{sx:.1f}" y1="{sy:.1f}" '
            f'x2="{tx:.1f}" y2="{ty:.1f}" />'
        )
        lines.append(
            f'<text class="edge-label" x="{mx:.1f}" y="{my:.1f}" text-anchor="middle">'
            f"{escape(edge.label)}</text>"
        )

    for node in nodes:
        x, y = positions[node.id]
        rect_x = x - (node_width * 0.4)
        rect_y = y - 20
        lines.append(
            f'<rect class="node" x="{rect_x:.1f}" y="{rect_y:.1f}" '
            f'width="{node_width * 0.8:.1f}" height="40" />'
        )
        lines.append(
            f'<text class="node-title" x="{x:.1f}" y="{y - 4:.1f}" text-anchor="middle">'
            f"{escape(node.label)}</text>"
        )
        lines.append(
            f'<text class="node-subtitle" x="{x:.1f}" y="{y + 11:.1f}" text-anchor="middle">'
            f"{escape(node.subtitle)}</text>"
        )

    lines.append("</svg>")
    return "\n".join(lines) + "\n"
