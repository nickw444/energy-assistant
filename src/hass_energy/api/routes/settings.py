from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from hass_energy.models.config import AppConfig, EmsConfig

router = APIRouter(prefix="/settings", tags=["settings"])


def get_app_config(request: Request) -> AppConfig:
    config: AppConfig | None = getattr(request.app.state, "app_config", None)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config missing",
        )
    return config


@router.get("", response_model=EmsConfig)
def read_settings(
    app_config: Annotated[AppConfig, Depends(get_app_config)],
) -> EmsConfig:
    return app_config.ems


@router.post("")
def update_settings() -> dict[str, str]:
    # Placeholder: config is read-only for now.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Settings updates via API are disabled; edit the YAML config directly.",
    )
