from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from energy_assistant.config import load_app_config
from energy_assistant.ems.fixtures.graphs import (
    build_logical_component_graph,
    build_topology_graph,
    render_graph_dot,
    render_graph_svg,
)
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.inputs.fixtures import load_fixture_input_provider


def test_fixture_graphs_emit_expected_dot() -> None:
    config_path = Path("tests/fixtures/ems/nwhass/short-horizon/config.yaml")
    fixture_path = Path("tests/fixtures/ems/nwhass/short-horizon/input.json")
    app_config = load_app_config(config_path)
    input_provider, captured_at = load_fixture_input_provider(path=fixture_path)
    planner = EmsMilpPlanner(
        input_provider=input_provider,
        system_factory=EmsSystemFactory.create(app_config),
    )

    built = planner.build_snapshot(
        now=datetime.fromisoformat(captured_at) if captured_at else None,
    )

    logical_graph = build_logical_component_graph(built.system)
    topology_graph = build_topology_graph(built.snapshot.graph)
    logical_dot = render_graph_dot(logical_graph)
    topology_dot = render_graph_dot(topology_graph)

    assert logical_graph.title == "Logical Component Graph"
    assert {node.id for node in logical_graph.nodes} >= {
        "switchboard",
        "grid",
        "base_load",
        "primary",
        "pv_primary",
        "battery_primary",
        "tessie",
    }
    assert "logical_component_graph" in logical_dot
    assert 'kind: ev' in logical_dot
    assert 'id: tessie' in logical_dot
    assert "#dcfce7" in logical_dot
    assert "EV incentives" not in logical_dot
    assert "Battery reserve" not in logical_dot
    assert "shape=point" in topology_dot
    assert 'label="ev_tessie_link.directional_limit' in topology_dot
    assert 'label="grid_link.import_soft_limit' in topology_dot
    assert 'label="pv_primary_link.curtail_tracking' in topology_dot
    assert 'label="battery_primary_link.efficiency' in topology_dot
    assert 'label="base_load_link.fixed_flow' in topology_dot
    assert 'xlabel=' not in topology_dot
    assert 'K=5.4' in topology_dot
    assert 'esep="+56"' in topology_dot
    assert 'sep=2.60' in topology_dot
    assert 'pad=0.35' in topology_dot
    assert 'len=7.8' in topology_dot
    assert 'headlabel=' not in topology_dot
    assert 'taillabel=' not in topology_dot
    assert "battery reserve" in topology_dot
    assert "ev incentives" in topology_dot
    assert "PV primary" not in topology_dot
    assert "Tessie" not in topology_dot
    assert "role: producer" in topology_dot
    assert "role: prosumer" in topology_dot
    assert "#fef3c7" in topology_dot
    assert "#dcfce7" in topology_dot
    assert "segment:primary_acdc:000:directional_limit" not in {
        node.id for node in topology_graph.nodes
    }
    assert any(
        edge.source_id == "primary_dc"
        and edge.target_id == "switchboard"
        and edge.label is not None
        and edge.label == "primary_acdc.directional_limit\ndirectional limit"
        for edge in topology_graph.edges
    )
    pv_segment_nodes = [node.id for node in topology_graph.nodes if "pv_primary_link" in node.id]
    assert pv_segment_nodes == [
        "segment:pv_primary_link:000:directional_limit",
        "segment:pv_primary_link:001:upper_bound",
    ]
    pv_segment_edges = [
        (edge.source_id, edge.target_id, edge.label)
        for edge in topology_graph.edges
        if "pv_primary_link" in edge.source_id
        or "pv_primary_link" in edge.target_id
        or (edge.source_id, edge.target_id) == ("pv_primary", "primary_dc")
    ]
    assert pv_segment_edges == [
        (
            "pv_primary",
            "segment:pv_primary_link:000:directional_limit",
            "pv_primary_link.directional_limit\ndirectional limit",
        ),
        (
            "segment:pv_primary_link:000:directional_limit",
            "segment:pv_primary_link:001:upper_bound",
            "pv_primary_link.upper_bound\nupper bound",
        ),
        (
            "segment:pv_primary_link:001:upper_bound",
            "primary_dc",
            "pv_primary_link.curtail_tracking\ncurtail tracking",
        ),
    ]


def test_fixture_graphs_render_expected_svg() -> None:
    if shutil.which("neato") is None:
        pytest.skip("Graphviz is not installed.")

    config_path = Path("tests/fixtures/ems/nwhass/short-horizon/config.yaml")
    fixture_path = Path("tests/fixtures/ems/nwhass/short-horizon/input.json")
    app_config = load_app_config(config_path)
    input_provider, captured_at = load_fixture_input_provider(path=fixture_path)
    planner = EmsMilpPlanner(
        input_provider=input_provider,
        system_factory=EmsSystemFactory.create(app_config),
    )

    built = planner.build_snapshot(
        now=datetime.fromisoformat(captured_at) if captured_at else None,
    )

    logical_svg = render_graph_svg(build_logical_component_graph(built.system))
    topology_svg = render_graph_svg(build_topology_graph(built.snapshot.graph))

    assert logical_svg.startswith('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
    assert "<svg " in logical_svg
    assert "<svg " in topology_svg
    assert "<!--" not in logical_svg
    assert "<!--" not in topology_svg
    assert "Switchboard" in logical_svg
    assert "EV incentives" not in logical_svg
    assert "Battery reserve" not in logical_svg
    assert "grid_link.directional_limit" in topology_svg
    assert "primary_acdc.directional_limit" in topology_svg
    assert "grid_link.import_soft_limit" in topology_svg
    assert "ev_tessie_link.directional_limit" in topology_svg
    assert "ev_tessie_link.charge_control" in topology_svg
    assert "pv_primary_link.upper_bound" in topology_svg
    assert "pv_primary_link.curtail_tracking" in topology_svg
    assert "directional limit" in topology_svg
    assert "charge control" in topology_svg
    assert "upper bound" in topology_svg
    assert "battery reserve" in topology_svg
    assert "ev incentives" in topology_svg
    assert "a->b 10.0kW" not in topology_svg
    assert "rt 0.0kW" not in topology_svg
    assert "PV primary" not in topology_svg
    assert "Tessie" not in topology_svg


def test_render_graph_svg_requires_graphviz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing_graphviz(binary_name: str) -> None:
        _ = binary_name
        return None

    monkeypatch.setattr(
        "energy_assistant.ems.fixtures.graphs.shutil.which",
        _missing_graphviz,
    )

    with pytest.raises(RuntimeError, match="Graphviz is required"):
        render_graph_svg(
            build_logical_component_graph(_build_test_system())
        )


def test_render_graph_svg_is_deterministic() -> None:
    if shutil.which("neato") is None:
        pytest.skip("Graphviz is not installed.")

    config_path = Path("tests/fixtures/ems/nwhass/short-horizon/config.yaml")
    fixture_path = Path("tests/fixtures/ems/nwhass/short-horizon/input.json")
    app_config = load_app_config(config_path)
    input_provider, captured_at = load_fixture_input_provider(path=fixture_path)
    planner = EmsMilpPlanner(
        input_provider=input_provider,
        system_factory=EmsSystemFactory.create(app_config),
    )

    built = planner.build_snapshot(
        now=datetime.fromisoformat(captured_at) if captured_at else None,
    )

    graph = build_topology_graph(built.snapshot.graph)
    assert render_graph_svg(graph) == render_graph_svg(graph)


def _build_test_system():
    config_path = Path("tests/fixtures/ems/nwhass/short-horizon/config.yaml")
    fixture_path = Path("tests/fixtures/ems/nwhass/short-horizon/input.json")
    app_config = load_app_config(config_path)
    input_provider, captured_at = load_fixture_input_provider(path=fixture_path)
    planner = EmsMilpPlanner(
        input_provider=input_provider,
        system_factory=EmsSystemFactory.create(app_config),
    )
    built = planner.build_snapshot(
        now=datetime.fromisoformat(captured_at) if captured_at else None,
    )
    return built.system
