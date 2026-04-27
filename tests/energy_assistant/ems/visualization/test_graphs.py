from __future__ import annotations

from datetime import datetime
from pathlib import Path

from energy_assistant.config import load_app_config
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.ems.topology.graph import EnergyGraph
from energy_assistant.ems.topology.ids import NodeId
from energy_assistant.ems.topology.nodes import Node
from energy_assistant.ems.topology.policies import DirectionalLimit
from energy_assistant.ems.visualization import (
    write_logical_component_graph_svg,
    write_topological_energy_graph_svg,
)


def test_write_logical_component_graph_svg(tmp_path: Path) -> None:
    app_config = load_app_config(
        Path("tests/fixtures/ems/nwhass/short-horizon-low-pv/config.yaml")
    )
    output = tmp_path / "logical_component_graph.svg"

    write_logical_component_graph_svg(app_config, output)

    content = output.read_text(encoding="utf-8")
    assert content.lstrip().startswith("<?xml")
    assert "pv_primary" in content
    assert "primary" in content
    assert "switchboard" in content
    assert "#eff6ff" in content
    assert "#fefce8" in content
    assert "#ecfdf5" in content


def test_write_topological_energy_graph_svg(tmp_path: Path) -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    )
    graph = EnergyGraph()
    graph.add_element(
        Node(
            horizon=horizon,
            id=NodeId("switchboard"),
            name="AC Switchboard",
            node_role="bus",
        )
    )
    graph.add_element(
        Node(
            horizon=horizon,
            id=NodeId("load"),
            name="Base Load",
            node_role="consumer",
        )
    )
    graph.add_element(
        Connection(
            horizon=horizon,
            id="load_link",
            a_node_id=NodeId("switchboard"),
            b_node_id=NodeId("load"),
            policies={
                "directional_limit": DirectionalLimit(
                    max_a_to_b_kw=None,
                    max_b_to_a_kw=0.0,
                )
            },
        )
    )
    output = tmp_path / "topological_energy_graph.svg"

    write_topological_energy_graph_svg(graph, output)

    content = output.read_text(encoding="utf-8")
    assert content.lstrip().startswith("<?xml")
    assert "load_link" in content
    assert "directional_limit" in content
    assert "switchboard" in content
