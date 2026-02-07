from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from energy_assistant.api.dependencies import get_config
from energy_assistant.models.config import AppConfig, EmsConfig

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=EmsConfig)
def read_settings(
    app_config: Annotated[AppConfig, Depends(get_config)],
) -> EmsConfig:
    return app_config.ems


@router.post("")
def update_settings() -> dict[str, str]:
    # Placeholder: config is read-only for now.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Settings updates via API are disabled; edit the YAML config directly.",
    )
