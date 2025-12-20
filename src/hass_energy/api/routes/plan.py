from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from hass_energy.worker import Worker

router = APIRouter(prefix="/plan", tags=["plan"])


def get_worker(request: Request) -> Worker:
    worker: Worker | None = getattr(request.app.state, "worker", None)
    if worker:
        return worker
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Worker not available",
    )


@router.post("/trigger")
def trigger_plan(
    worker: Annotated[Worker, Depends(get_worker)],
) -> dict[str, object]:
    plan = worker.trigger_once()
    return {"message": "plan generated", "plan": plan}
