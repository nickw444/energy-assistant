from __future__ import annotations

from fastapi import FastAPI

from energy_assistant.api.dependencies import GlobalDependencies
from energy_assistant.api.routes import plan, settings
from energy_assistant.models.config import AppConfig
from energy_assistant.worker import Worker


def create_app(app_config: AppConfig, worker: Worker | None = None) -> FastAPI:
    app = FastAPI(title="Energy Assistant")
    app.state.dependencies = GlobalDependencies(config=app_config, worker=worker)
    app.include_router(plan.router)
    app.include_router(settings.router)
    return app
