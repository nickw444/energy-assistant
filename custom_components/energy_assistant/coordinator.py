"""Data update coordinator for the Energy Assistant integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .energy_assistant_client import (
    EmsPlanOutput,
    EnergyAssistantApiClient,
    PlanAwaitResponse,
    PlanLatestResponse,
    TimestepPlan,
)

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

LONG_POLL_TIMEOUT = 75
LONG_POLL_RETRY_DELAY = 5


@dataclass(slots=True)
class PlanPayload:
    response: PlanLatestResponse
    plan_dump: dict[str, Any]


class EnergyAssistantCoordinator(DataUpdateCoordinator[PlanPayload | None]):
    """Coordinator that uses continuous long-polling to fetch plan updates.

    The coordinator runs a background long-poll loop that continuously waits for
    new plans from the server. When a new plan arrives, it updates the data and
    notifies all listeners immediately. The standard update_interval serves as a
    fallback safety net.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: EnergyAssistantApiClient,
        interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="energy_assistant_plan",
            update_interval=interval,
        )
        self._client = client
        self._last_generated_at: str | None = None
        self._long_poll_task: asyncio.Task[None] | None = None

    async def _async_update_data(self) -> PlanPayload | None:
        """Fallback fetch for when long-poll loop isn't running or times out."""
        try:
            response = await self._client.get_latest_plan()
        except (aiohttp.ClientError, ValueError) as exc:
            raise UpdateFailed(f"Failed to fetch EMS plan: {exc}") from exc
        if response is None:
            return None
        self._last_generated_at = response.plan.generated_at.isoformat()
        _LOGGER.debug("Fallback fetch returned plan generated_at=%s", self._last_generated_at)
        return PlanPayload(
            response=response,
            plan_dump=response.plan.model_dump(mode="json"),
        )

    def start_long_poll_loop(self) -> None:
        """Start the background long-poll loop."""
        if self._long_poll_task is not None and not self._long_poll_task.done():
            return
        self._long_poll_task = self.hass.async_create_task(self._run_long_poll_loop())
        _LOGGER.debug("Long-poll loop started")

    def stop_long_poll_loop(self) -> None:
        """Stop the background long-poll loop."""
        if self._long_poll_task is not None and not self._long_poll_task.done():
            self._long_poll_task.cancel()
            _LOGGER.debug("Long-poll loop stopped")

    async def _run_long_poll_loop(self) -> None:
        """Continuously long-poll for plan updates."""
        while True:
            try:
                await self._long_poll_once()
            except asyncio.CancelledError:
                break
            except aiohttp.ClientError as exc:
                _LOGGER.debug("Long-poll request failed: %s, retrying...", exc)
                await asyncio.sleep(LONG_POLL_RETRY_DELAY)
            except Exception:
                _LOGGER.exception("Long-poll loop error, retrying...")
                await asyncio.sleep(LONG_POLL_RETRY_DELAY)

    async def _long_poll_once(self) -> None:
        """Perform a single long-poll request and update data if new plan arrives."""
        _LOGGER.debug(
            "Long-polling for plan updates (since=%s, timeout=%ds)",
            self._last_generated_at,
            LONG_POLL_TIMEOUT,
        )
        await_response: PlanAwaitResponse | None = await self._client.await_plan(
            since=self._last_generated_at,
            timeout=LONG_POLL_TIMEOUT,
        )
        if await_response is None:
            _LOGGER.debug("Long-poll timed out, no new plan")
            return

        response = PlanLatestResponse(
            run=await_response.run,
            plan=await_response.plan,
            intent=await_response.intent,
        )
        self._last_generated_at = response.plan.generated_at.isoformat()
        _LOGGER.debug("Long-poll received new plan (generated_at=%s)", self._last_generated_at)
        self.async_set_updated_data(
            PlanPayload(
                response=response,
                plan_dump=response.plan.model_dump(mode="json"),
            )
        )


def get_timestep0(plan: EmsPlanOutput) -> TimestepPlan | None:
    if not plan.timesteps:
        return None
    return plan.timesteps[0]


def sorted_items[T](values: dict[str, T]) -> list[tuple[str, T]]:
    return sorted(values.items(), key=lambda item: str(item[0]))


def build_plan_series(
    plan: EmsPlanOutput,
    getter: Callable[[TimestepPlan], Any],
    transform: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for step in plan.timesteps:
        value = getter(step)
        if transform is not None:
            value = transform(value)
        series.append(
            {
                "value": value,
                "start": step.start.isoformat(),
                "duration_s": step.duration_s,
            }
        )
    return series


def inverter_value_getter(
    inverter_name: str,
    attribute: str,
) -> Callable[[PlanLatestResponse], Any]:
    def _get(response: PlanLatestResponse) -> Any:
        step = get_timestep0(response.plan)
        if step is None:
            return None
        inverter = step.inverters.get(inverter_name)
        if inverter is None:
            return None
        return getattr(inverter, attribute, None)

    return _get


def inverter_step_getter(
    inverter_name: str,
    attribute: str,
) -> Callable[[TimestepPlan], Any]:
    def _get(step: TimestepPlan) -> Any:
        inverter = step.inverters.get(inverter_name)
        if inverter is None:
            return None
        return getattr(inverter, attribute, None)

    return _get


def ev_value_getter(
    ev_name: str,
    attribute: str,
) -> Callable[[PlanLatestResponse], Any]:
    def _get(response: PlanLatestResponse) -> Any:
        step = get_timestep0(response.plan)
        if step is None:
            return None
        ev = step.loads.evs.get(ev_name)
        if ev is None:
            return None
        return getattr(ev, attribute, None)

    return _get


def ev_step_getter(
    ev_name: str,
    attribute: str,
) -> Callable[[TimestepPlan], Any]:
    def _get(step: TimestepPlan) -> Any:
        ev = step.loads.evs.get(ev_name)
        if ev is None:
            return None
        return getattr(ev, attribute, None)

    return _get


def intent_inverter_value_getter(
    inverter_name: str,
    attribute: str,
) -> Callable[[PlanLatestResponse], Any]:
    def _get(response: PlanLatestResponse) -> Any:
        inverter = response.intent.inverters.get(inverter_name)
        if inverter is None:
            return None
        return getattr(inverter, attribute, None)

    return _get


def intent_load_value_getter(
    load_name: str,
    attribute: str,
) -> Callable[[PlanLatestResponse], Any]:
    def _get(response: PlanLatestResponse) -> Any:
        load = response.intent.loads.get(load_name)
        if load is None:
            return None
        return getattr(load, attribute, None)

    return _get
