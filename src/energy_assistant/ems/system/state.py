from __future__ import annotations

from typing import Protocol, cast


class SupportsSolveState[TSolveState](Protocol):
    id: str


class SolveStateStore:
    """Typed solve-state registry keyed by component id."""

    def __init__(self) -> None:
        self._states: dict[str, object] = {}

    def put[TSolveState](
        self,
        component: SupportsSolveState[TSolveState],
        solve_state: TSolveState,
    ) -> None:
        self._states[component.id] = solve_state

    def get[TSolveState](
        self,
        component: SupportsSolveState[TSolveState],
    ) -> TSolveState:
        try:
            return cast(TSolveState, self._states[component.id])
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(f"Missing solve state for component {component.id!r}") from exc


# Backwards-compatible name retained for existing imports.
EmsSystemSolveState = SolveStateStore
