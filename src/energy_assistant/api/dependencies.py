from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from energy_assistant.models.config import AppConfig
from energy_assistant.worker import Worker


@dataclass(frozen=True, slots=True)
class GlobalDependencies:
    config: AppConfig
    worker: Worker | None


def _get_globals(request: Request) -> GlobalDependencies:
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Global dependencies missing",
        )
    if not isinstance(deps, GlobalDependencies):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Global dependencies misconfigured",
        )
    return deps


def get_config(dependencies: Annotated[GlobalDependencies, Depends(_get_globals)]) -> AppConfig:
    return dependencies.config


def get_worker(dependencies: Annotated[GlobalDependencies, Depends(_get_globals)]) -> Worker:
    worker = dependencies.worker
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Worker not available",
        )
    return worker
