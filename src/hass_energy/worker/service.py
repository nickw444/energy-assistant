from __future__ import annotations

import json
import logging
import threading
from typing import Any

from hass_energy.config import AppConfig, EnergySystemConfig
from hass_energy.lib import HomeAssistantClient
from hass_energy.worker.milp import MilpPlanner

logger = logging.getLogger(__name__)


class Worker:
    """Background worker that polls data and runs the MILP planner stub."""

    def __init__(
        self,
        *,
        app_config: AppConfig,
        home_assistant_client: HomeAssistantClient | None = None,
        planner: MilpPlanner | None = None,
    ) -> None:
        self.app_config = app_config
        self.home_assistant_client = home_assistant_client or HomeAssistantClient()
        self.planner = planner or MilpPlanner()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.info("Worker already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Worker started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Worker stopped")

    def trigger_once(self) -> dict[str, Any]:
        ha_config = self.app_config.homeassistant
        state = self.home_assistant_client.fetch_realtime_state(ha_config)
        history = self.home_assistant_client.fetch_history(ha_config)
        plan = self._build_plan(self.app_config.energy, state, history)
        self._persist_plan(plan)
        return plan

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            config = self.app_config.energy
            logger.debug("Worker loop tick with poll interval %s", config.poll_interval_seconds)
            self.trigger_once()
            self._stop_event.wait(timeout=float(config.poll_interval_seconds))

    def _build_plan(
        self,
        config: EnergySystemConfig,
        realtime_state: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        logger.info("Generating plan via MILP solver")
        return self.planner.generate_plan(config, realtime_state, history)

    def _persist_plan(self, plan: dict[str, Any]) -> None:
        plan_dir = self.app_config.server.data_dir / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        path = plan_dir / "latest.json"
        path.write_text(json_dumps(plan))
        logger.debug("Persisted plan to %s", path)


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)
