from __future__ import annotations

from abc import ABC, abstractmethod

from energy_assistant.ems.components.context import GraphBuildContext, PlanContext
from energy_assistant.ems.horizon import Horizon
from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.system.state import SolveStateStore, SupportsSolveState
from energy_assistant.ems.topology.graph import EnergyGraph, GraphElement


class EmsComponent[TSolveState, TPlanExport](ABC, SupportsSolveState[TSolveState]):
    """Persistent logical component that contributes to each solve.

    Components keep configuration and stable ids. For each solve they emit fresh topology
    elements, optionally add cross-component fragments after all components have built their local
    graph, then extract their typed plan payload from the solved snapshot.
    """

    id: str

    @abstractmethod
    def create_graph_elements(
        self,
        *,
        horizon: Horizon,
        inputs: AppliedInputRegistry,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], TSolveState]:
        """Build this component's local solve-scoped topology elements."""

    def create_graph_fragments(
        self,
        *,
        graph: EnergyGraph,
        build_ctx: GraphBuildContext,
        solve_states: SolveStateStore,
    ) -> list[GraphElement]:
        """Add late-bound graph fragments that need other components' graph elements."""
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
        """Extract this component's export payload from the solved model."""
