from __future__ import annotations

from datetime import UTC, datetime

from energy_assistant.ems.components.context import PlanContext
from energy_assistant.ems.components.grid import GridComponent, GridSolveState
from energy_assistant.ems.components.grid.price_bindings import PriceBindingApplicator
from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.ems.components.switchboard import SwitchboardComponent
from energy_assistant.ems.horizon import Horizon, HorizonFactory
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


def _grid_solve_state(
    *,
    horizon: Horizon,
    grid: GridComponent,
    import_price: float,
    export_price: float,
) -> GridSolveState:
    connection = Connection(
        horizon=horizon,
        id=f"{grid.id}_link",
        a_node_id=grid.switchboard.bus_id,
        b_node_id=grid.node_id,
    )
    return GridSolveState(
        connection=connection,
        price_import_raw=[import_price],
        price_export_raw=[export_price],
        price_import_effective=[import_price],
        price_export_effective=[export_price],
        import_allowed=[True],
    )


def test_plan_context_looks_up_components_without_topology_index() -> None:
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    )
    switchboard_a = _switchboard("switchboard_a")
    switchboard_b = _switchboard("switchboard_b")
    local_grid = _grid_component(
        component_id="local_grid",
        switchboard=switchboard_a,
        max_export_kw=4.0,
    )
    remote_grid = _grid_component(
        component_id="remote_grid",
        switchboard=switchboard_b,
        max_export_kw=9.0,
    )
    solve_states = SolveStateStore()
    solve_states.put(
        local_grid,
        _grid_solve_state(
            horizon=horizon,
            grid=local_grid,
            import_price=0.2,
            export_price=0.1,
        ),
    )
    solve_states.put(
        remote_grid,
        _grid_solve_state(
            horizon=horizon,
            grid=remote_grid,
            import_price=0.4,
            export_price=0.8,
        ),
    )
    plan_ctx = PlanContext(
        components={
            "switchboard_a": switchboard_a,
            "switchboard_b": switchboard_b,
            "local_grid": local_grid,
            "remote_grid": remote_grid,
        },
        solve_states=solve_states,
    )

    assert plan_ctx.components["local_grid"] is local_grid
    assert plan_ctx.components["remote_grid"] is remote_grid
    assert plan_ctx.components["switchboard_a"] is switchboard_a
    assert plan_ctx.solve_states.get(local_grid).connection.id == "local_grid_link"
    assert plan_ctx.solve_states.get(remote_grid).connection.id == "remote_grid_link"
