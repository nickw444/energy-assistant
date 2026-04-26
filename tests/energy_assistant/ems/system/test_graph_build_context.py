from __future__ import annotations

from datetime import UTC, datetime

from energy_assistant.ems.components.context import GraphBuildContext
from energy_assistant.ems.components.grid import GridComponent
from energy_assistant.ems.components.grid.price_bindings import PriceBindingApplicator
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.connection import Connection
from energy_assistant.models.plant import (
    GridComponentConfig,
    GridConstraintsConfig,
    InputReference,
    PriceBindingConfig,
)


def _grid_config(*, name: str, max_export_kw: float) -> GridComponentConfig:
    return GridComponentConfig(
        type="grid",
        connection="switchboard",
        constraints=GridConstraintsConfig(max_import_kw=10.0, max_export_kw=max_export_kw),
        price_import=PriceBindingConfig(source=InputReference(source=f"{name}_import")),
        price_export=PriceBindingConfig(source=InputReference(source=f"{name}_export")),
    )


def _switchboard(component_id: str) -> SwitchboardComponent:
    return SwitchboardComponent(component_id=component_id)


def _grid_component(
    *,
    component_id: str,
    switchboard: SwitchboardComponent,
    max_export_kw: float,
) -> GridComponent:
    return GridComponent(
        component_id=component_id,
        switchboard=switchboard,
        grid=_grid_config(name=component_id, max_export_kw=max_export_kw),
        time_window_matcher=TimeWindowMatcher(),
        price_binding_applicator=PriceBindingApplicator(),
    )


def test_graph_build_context_preserves_multiple_connections_per_component() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    switchboard = _switchboard("switchboard")
    multi_grid = _grid_component(
        component_id="multi",
        switchboard=switchboard,
        max_export_kw=9.0,
    )
    build_ctx = GraphBuildContext(
        components={"switchboard": switchboard, "multi": multi_grid},
        solve_states=SolveStateStore(),
    )
    first = Connection(
        horizon=horizon,
        id="multi_primary",
        a_node_id=switchboard.bus_id,
        b_node_id=multi_grid.node_id,
    )
    second = Connection(
        horizon=horizon,
        id="multi_secondary",
        a_node_id=switchboard.bus_id,
        b_node_id=multi_grid.node_id,
    )

    build_ctx.register("multi", [first, second])

    assert build_ctx.components_of_type(GridComponent) == (multi_grid,)
    assert build_ctx.connections("multi") == (first, second)
