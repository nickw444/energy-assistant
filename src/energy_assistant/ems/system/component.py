from __future__ import annotations

from abc import ABC, abstractmethod

from energy_assistant.ems.inputs.models import AppliedInputRegistry
from energy_assistant.ems.milp.snapshot import ModelSnapshot
from energy_assistant.ems.planning.horizon import Horizon
from energy_assistant.ems.system.state import SupportsSolveState
from energy_assistant.ems.system.topology import ComponentTopology, GraphBuildContext, PlanContext
from energy_assistant.ems.topology.graph import GraphElement


class EmsComponent[TSolveState, TPlanExport](ABC, SupportsSolveState[TSolveState]):
    """Typed EMS component contract."""

    id: str

    @abstractmethod
    def describe_topology(self) -> ComponentTopology:
        """Describe how this component participates in the plant topology."""

    @abstractmethod
    def update_inputs(self, *, horizon: Horizon, inputs: AppliedInputRegistry) -> None:
        """Populate per-solve input state."""

    @abstractmethod
    def build_graph(
        self,
        *,
        horizon: Horizon,
        build_ctx: GraphBuildContext,
    ) -> tuple[list[GraphElement], TSolveState]:
        """Build solve-scoped graph elements and return the component solve state."""

    @abstractmethod
    def build_plan(
        self,
        snapshot: ModelSnapshot,
        *,
        solve_state: TSolveState,
        plan_ctx: PlanContext,
    ) -> TPlanExport:
        """Export a typed plan payload from the solved model."""
