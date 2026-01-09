from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from hass_energy.api.routes.plan_dto import (
    PlanAwaitResponseDto,
    PlanLatestResponseDto,
    PlanRunResponseDto,
    PlanRunStateDto,
)
from hass_energy.worker import PlanRunState, Worker

router = APIRouter(prefix="/plan", tags=["plan"])


def get_worker(request: Request) -> Worker:
    worker: Worker | None = getattr(request.app.state, "worker", None)
    if worker:
        return worker
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Worker not available",
    )


def _run_to_dto(run: PlanRunState) -> PlanRunStateDto:
    return PlanRunStateDto(
        run_id=run.run_id,
        status=run.status,
        accepted_at=run.accepted_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        message=run.message,
    )


@router.post("/run", response_model=PlanRunResponseDto, status_code=status.HTTP_202_ACCEPTED)
async def run_plan(
    worker: Annotated[Worker, Depends(get_worker)],
) -> PlanRunResponseDto:
    run_state, already_running = await worker.trigger_run()
    return PlanRunResponseDto(run=_run_to_dto(run_state), already_running=already_running)


@router.get("/latest", response_model=PlanLatestResponseDto)
async def latest_plan(
    worker: Annotated[Worker, Depends(get_worker)],
) -> PlanLatestResponseDto:
    latest = await worker.get_latest()
    if latest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No plan available")
    run_state, plan = latest
    return PlanLatestResponseDto(
        run=_run_to_dto(run_state),
        plan=plan,
    )


def _parse_since(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


@router.get("/await", response_model=PlanAwaitResponseDto)
async def await_plan(
    worker: Annotated[Worker, Depends(get_worker)],
    since: str | None = None,
    timeout: int = 30,
) -> PlanAwaitResponseDto | Response:
    since_ts = _parse_since(since)
    try:
        latest = await worker.await_latest(since_ts=since_ts, timeout=timeout)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    if latest is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    run_state, plan = latest
    return PlanAwaitResponseDto(
        run=_run_to_dto(run_state),
        plan=plan,
    )
