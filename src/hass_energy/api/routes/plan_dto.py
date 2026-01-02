from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PlanRunStateDto(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "failed"]
    accepted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str | None = None

    model_config = ConfigDict(extra="forbid")


class PlanResultDto(BaseModel):
    generated_at: float
    status: str
    objective: float | None = None
    plan: dict[str, object]

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_plan(cls, plan: object) -> "PlanResultDto":
        if not isinstance(plan, dict):
            return cls(generated_at=0.0, status="unknown", objective=None, plan={})
        generated_at = float(plan.get("generated_at", 0.0) or 0.0)
        status = str(plan.get("status") or "unknown")
        objective_raw = plan.get("objective")
        objective = float(objective_raw) if isinstance(objective_raw, (int, float)) else None
        return cls(
            generated_at=generated_at,
            status=status,
            objective=objective,
            plan=plan,
        )


class PlanRunResponseDto(BaseModel):
    run: PlanRunStateDto
    already_running: bool = False

    model_config = ConfigDict(extra="forbid")


class PlanLatestResponseDto(BaseModel):
    run: PlanRunStateDto
    result: PlanResultDto

    model_config = ConfigDict(extra="forbid")


class PlanAwaitResponseDto(BaseModel):
    run: PlanRunStateDto
    result: PlanResultDto

    model_config = ConfigDict(extra="forbid")
