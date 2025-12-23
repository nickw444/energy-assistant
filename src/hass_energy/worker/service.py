from __future__ import annotations

import logging
from typing import Any

from hass_energy.models.config import AppConfig
from hass_energy.lib import HomeAssistantClient
from hass_energy.worker.milp import MilpPlanner

logger = logging.getLogger(__name__)


class Worker:
    """Placeholder worker until the background service is redesigned."""

    def __init__(
        self,
        *,
        app_config: AppConfig,
        home_assistant_client: HomeAssistantClient,
        planner: MilpPlanner | None = None,
    ) -> None:
        self.app_config = app_config
        self.home_assistant_client = home_assistant_client
        self.planner = planner

    def start(self) -> None:
        logger.info("Worker start requested (no-op placeholder)")

    def stop(self) -> None:
        logger.info("Worker stop requested (no-op placeholder)")

    def trigger_once(self) -> dict[str, Any]:
        logger.info("Worker trigger requested (no-op placeholder)")
        return {}
