from __future__ import annotations

import pytest

from energy_assistant.ems.system.state import EmsSystemSolveState, SolveStateStore


class _Component:
    def __init__(self, component_id: str) -> None:
        self.id = component_id


def test_solve_state_store_put_get_round_trip() -> None:
    store = SolveStateStore()
    component = _Component("battery")
    payload = {"soc": 42.0}

    store.put(component, payload)
    assert store.get(component) == payload


def test_solve_state_store_missing_key_message() -> None:
    store = SolveStateStore()
    component = _Component("missing")

    with pytest.raises(KeyError, match="Missing solve state"):
        store.get(component)


def test_backwards_compatible_alias_points_to_store() -> None:
    assert EmsSystemSolveState is SolveStateStore
