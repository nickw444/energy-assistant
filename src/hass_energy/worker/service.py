from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from hass_energy.ems.models import EmsPlanOutput
from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.lib.home_assistant_ws import HomeAssistantWebSocketClient
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.models.config import AppConfig

logger = logging.getLogger(__name__)

_SCHEDULE_INTERVAL = timedelta(minutes=1)
_PRICE_DEBOUNCE_SECONDS = 0.75


@dataclass(slots=True)
class PlanRunState:
    run_id: str
    status: Literal["queued", "running", "completed", "failed"]
    accepted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str | None = None


class Worker:
    """Background worker for EMS planning."""

    def __init__(
        self,
        *,
        app_config: AppConfig,
        resolver: ValueResolver,
    ) -> None:
        self._app_config = app_config
        self._resolver = resolver
        self._resolver.mark_for_hydration(app_config)

        self._condition = asyncio.Condition()
        self._in_progress = False
        self._current_run: PlanRunState | None = None
        self._latest_run: PlanRunState | None = None
        self._latest_plan: EmsPlanOutput | None = None
        self._schedule_task: asyncio.Task[None] | None = None
        self._price_watcher_task: asyncio.Task[None] | None = None
        self._price_debounce_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

        self._price_entity_ids = {
            app_config.plant.grid.realtime_price_import.entity,
            app_config.plant.grid.realtime_price_export.entity,
        }
        self._ha_ws_client = HomeAssistantWebSocketClient(config=app_config.homeassistant)

    def start(self) -> None:
        if self._schedule_task and not self._schedule_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.info("Worker start requested without running event loop; skipping schedule")
            return
        self._loop = loop
        self._stop_event.clear()
        self._schedule_task = loop.create_task(self._run_schedule())
        self._price_watcher_task = loop.create_task(self._run_price_watcher())
        logger.info("Worker started (schedule + price watcher)")

    def stop(self) -> None:
        if self._loop is None or self._schedule_task is None:
            logger.info("Worker stop requested (no schedule)")
            return
        self._stop_event.set()
        if self._price_watcher_task and not self._price_watcher_task.done():
            self._price_watcher_task.cancel()
        if self._price_debounce_task and not self._price_debounce_task.done():
            self._price_debounce_task.cancel()
        logger.info("Worker stop requested")

    async def trigger_run(self) -> tuple[PlanRunState, bool]:
        async with self._condition:
            if self._in_progress and self._current_run is not None:
                return self._current_run, True
            now = datetime.now(UTC)
            run_state = PlanRunState(
                run_id=_new_run_id(),
                status="running",
                accepted_at=now,
                started_at=now,
            )
            self._in_progress = True
            self._current_run = run_state

        asyncio.create_task(self._run_once(run_state))
        return run_state, False

    async def get_latest(self) -> tuple[PlanRunState, EmsPlanOutput] | None:
        async with self._condition:
            if self._latest_run is None or self._latest_plan is None:
                return None
            return self._latest_run, self._latest_plan

    async def await_latest(
        self,
        *,
        since_ts: float,
        timeout: int,
    ) -> tuple[PlanRunState, EmsPlanOutput] | None:
        async with self._condition:

            def _predicate() -> bool:
                return (
                    self._latest_plan is not None
                    and self._latest_run is not None
                    and _plan_generated_at(self._latest_plan) > since_ts
                ) or (self._current_run is not None and self._current_run.status == "failed")

            try:
                await asyncio.wait_for(self._condition.wait_for(_predicate), timeout=timeout)
            except TimeoutError:
                return None

            if (
                self._latest_plan is not None
                and self._latest_run is not None
                and _plan_generated_at(self._latest_plan) > since_ts
            ):
                return self._latest_run, self._latest_plan
            if self._current_run is not None and self._current_run.status == "failed":
                raise RuntimeError(self._current_run.message or "Plan run failed")
            return None

    async def _run_once(self, run_state: PlanRunState) -> None:
        try:
            plan = await asyncio.to_thread(self._solve_once_blocking)
            finished = datetime.now(UTC)
            completed_state = _update_run(
                run_state,
                status="completed",
                finished_at=finished,
            )
        except Exception as exc:  # pragma: no cover - unexpected runtime failures
            logger.exception("Worker plan run failed")
            finished = datetime.now(UTC)
            completed_state = _update_run(
                run_state,
                status="failed",
                finished_at=finished,
                message=str(exc),
            )
            plan = None

        async with self._condition:
            self._in_progress = False
            self._current_run = completed_state
            if plan is not None:
                self._latest_run = completed_state
                self._latest_plan = plan
            self._condition.notify_all()

    def _solve_once_blocking(self) -> EmsPlanOutput:
        self._resolver.hydrate_all()
        return EmsMilpPlanner(self._app_config, resolver=self._resolver).generate_ems_plan()

    async def _run_schedule(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.trigger_run()
            except Exception:  # pragma: no cover - safety net
                logger.exception("Scheduled EMS run failed to start")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=_SCHEDULE_INTERVAL.total_seconds(),
                )
            except TimeoutError:
                continue

    async def _run_price_watcher(self) -> None:
        logger.info("Price watcher started for entities: %s", self._price_entity_ids)
        try:
            async for state in self._ha_ws_client.subscribe_state_changes(self._price_entity_ids):
                if self._stop_event.is_set():
                    break
                logger.debug("Price entity changed: %s = %s", state["entity_id"], state["state"])
                self._schedule_debounced_replan()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Price watcher failed unexpectedly")

    def _schedule_debounced_replan(self) -> None:
        if self._price_debounce_task and not self._price_debounce_task.done():
            self._price_debounce_task.cancel()
        if self._loop is None:
            return
        self._price_debounce_task = self._loop.create_task(self._debounced_replan())

    async def _debounced_replan(self) -> None:
        try:
            await asyncio.sleep(_PRICE_DEBOUNCE_SECONDS)
            logger.info("Price change detected; triggering EMS plan run")
            await self.trigger_run()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Failed to trigger EMS plan run after price change")


def _new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")


def _update_run(
    run_state: PlanRunState,
    *,
    status: Literal["queued", "running", "completed", "failed"],
    finished_at: datetime | None = None,
    message: str | None = None,
) -> PlanRunState:
    return PlanRunState(
        run_id=run_state.run_id,
        status=status,
        accepted_at=run_state.accepted_at,
        started_at=run_state.started_at,
        finished_at=finished_at,
        message=message,
    )


def _plan_generated_at(plan: EmsPlanOutput) -> float:
    return plan.generated_at.timestamp()
