from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from energy_assistant.ems.models import EmsPlanOutput, PlanIntent


class PlanRunStateDto(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    accepted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str | None = None

    model_config = ConfigDict(extra="forbid")


class PlanRunResponseDto(BaseModel):
    run: PlanRunStateDto
    already_running: bool = False

    model_config = ConfigDict(extra="forbid")


class PlanLatestResponseDto(BaseModel):
    run: PlanRunStateDto
    plan: EmsPlanOutput
    intent: PlanIntent

    model_config = ConfigDict(extra="forbid")


class PlanAwaitResponseDto(BaseModel):
    run: PlanRunStateDto
    plan: EmsPlanOutput
    intent: PlanIntent

    model_config = ConfigDict(extra="forbid")
