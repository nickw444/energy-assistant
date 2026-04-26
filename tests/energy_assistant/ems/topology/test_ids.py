from __future__ import annotations

from energy_assistant.ems.topology.ids import NodeId


def test_node_id_behaves_as_string_newtype() -> None:
    node_id = NodeId("switchboard")
    assert isinstance(node_id, str)
    assert node_id == "switchboard"
