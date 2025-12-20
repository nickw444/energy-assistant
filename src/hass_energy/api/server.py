from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from hass_energy.api.routes import plan, settings
from hass_energy.config import AppConfig
from hass_energy.worker import Worker

logger = logging.getLogger(__name__)


def create_app(app_config: AppConfig, worker: Worker | None = None) -> FastAPI:
    background_worker = worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.app_config = app_config
        app.state.worker = background_worker
        if background_worker:
            logger.info("Starting worker from API lifespan startup")
            background_worker.start()
        try:
            yield
        finally:
            if background_worker:
                logger.info("Stopping worker from API lifespan shutdown")
                background_worker.stop()

    app = FastAPI(title="hass-energy", lifespan=lifespan)
    app.include_router(plan.router)
    app.include_router(settings.router)
    return app
