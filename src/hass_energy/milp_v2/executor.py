from __future__ import annotations

from typing import Any, cast

import pulp

from hass_energy.milp_v2.types import CompiledModel, PlanResult


class MilpExecutor:
    """Phase 2: execute a compiled MILP model and return an optimal plan."""

    def __init__(self) -> None:
        self._solver = pulp.PULP_CBC_CMD(msg=False)

    def solve(self, compiled: CompiledModel) -> PlanResult:
        """Solve the compiled model and build a plan (grid-only for now)."""
        status = compiled.solve(self._solver)  # type: ignore[no-untyped-call]
        objective_value = pulp.value(compiled.objective)  # type: ignore[arg-type, no-untyped-call]
        objective = float(objective_value) if objective_value is not None else None

        context = compiled.hass_context
        slots = self._build_slots(context)

        return PlanResult(
            status=pulp.LpStatus[status],
            objective=objective,
            slots=slots,
        )

    def _build_slots(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        slots = cast(list[dict[str, Any]], context.get("slots", []))
        grid_import_vars = cast(
            list[pulp.LpVariable],
            context.get("grid_import_vars", []),
        )
        grid_export_vars = cast(
            list[pulp.LpVariable],
            context.get("grid_export_vars", []),
        )

        plan_slots: list[dict[str, Any]] = []
        for idx, slot in enumerate(slots):
            import_kw = grid_import_vars[idx].value() if idx < len(grid_import_vars) else None
            export_kw = grid_export_vars[idx].value() if idx < len(grid_export_vars) else None
            plan_slots.append(
                {
                    "start": slot.get("start"),
                    "end": slot.get("end"),
                    "duration_h": slot.get("duration_h"),
                    "grid_import_kw": float(import_kw or 0.0),
                    "grid_export_kw": float(export_kw or 0.0),
                    "import_allowed": slot.get("import_allowed", True),
                }
            )
        return plan_slots
