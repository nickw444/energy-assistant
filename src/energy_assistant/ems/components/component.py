from __future__ import annotations

from abc import ABC, abstractmethod

from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.system.state import SolveStateStore, SupportsSolveState
from energy_assistant.ems.topology.graph import EnergyGraph, GraphElement


class EmsComponent[TSolveState, TPlanExport](ABC, SupportsSolveState[TSolveState]):
    """Typed EMS component contract."""

    id: str

    @abstractmethod
    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], TSolveState]:
        """Build solve-scoped physical topology elements from the current inputs."""

    def create_graph_fragments(
        self,
        *,
        graph: EnergyGraph,
        build_ctx: GraphBuildContext,
        solve_states: SolveStateStore,
    ) -> list[GraphElement]:
        _ = graph, build_ctx, solve_states
        return []

    @abstractmethod
    def extract_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: TSolveState,
        plan_ctx: PlanContext,
    ) -> TPlanExport:
        """Extract a typed plan payload from the solved model."""
