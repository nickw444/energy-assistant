from __future__ import annotations

from fastapi import FastAPI

from hass_energy.api.routes import plan, settings
from hass_energy.models.config import AppConfig
from hass_energy.worker import Worker


def create_app(app_config: AppConfig, worker: Worker | None = None) -> FastAPI:
    app = FastAPI(title="hass-energy")
    app.state.app_config = app_config
    app.state.worker = worker
    app.include_router(plan.router)
    app.include_router(settings.router)
    return app
