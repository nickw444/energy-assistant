from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pulp


class HassLpProblem(pulp.LpProblem):
    """LpProblem with attached planner context for execution."""

    hass_context: dict[str, Any]


CompiledModel = HassLpProblem


@dataclass(frozen=True)
class PlanResult:
    """Phase 2 output: optimal plan."""

    status: str
    objective: float | None
    slots: list[dict[str, Any]]
