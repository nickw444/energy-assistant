from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from energy_assistant.ems.components.component import EmsComponent
from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.horizon import Horizon, HorizonFactory
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.context import ModelContext
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.system.state import SolveStateStore
from energy_assistant.ems.topology.graph import EnergyGraph, GraphElement


@dataclass(frozen=True, slots=True)
class _FakeSolveState:
    value: int


class _FakeComponent(EmsComponent[_FakeSolveState, dict[str, int]]):

    def __init__(self) -> None:
        self.id = "fake"

    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], _FakeSolveState]:
        _ = horizon, inputs, build_ctx
        return [], _FakeSolveState(value=7)

    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: _FakeSolveState,
        plan_ctx: PlanContext,
    ) -> dict[str, int]:
        _ = snapshot, plan_ctx
        return {"value": solve_state.value}


def test_component_default_create_graph_fragments_returns_empty() -> None:
    component = _FakeComponent()
    fragments = component.create_graph_fragments(
        graph=EnergyGraph(),
        build_ctx=GraphBuildContext(components={}, solve_states=SolveStateStore()),
        solve_states=SolveStateStore(),
    )
    assert fragments == []


def test_component_extract_plan_uses_solve_state() -> None:
    component = _FakeComponent()
    horizon = HorizonFactory(timestep_minutes=60, horizon_minutes=60).build(
        now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    snapshot = ModelSnapshot(
        ctx=ModelContext(horizon=horizon),
        graph=EnergyGraph(),
    )
    plan = component.extract_plan(
        snapshot,
        solve_state=_FakeSolveState(value=11),
        plan_ctx=PlanContext(components={}, solve_states=SolveStateStore()),
    )
    assert plan == {"value": 11}
