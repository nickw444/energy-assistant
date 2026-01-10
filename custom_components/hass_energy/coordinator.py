"""Data update coordinator for the HASS Energy integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .hass_energy_client import (
    EmsPlanOutput,
    HassEnergyApiClient,
    PlanAwaitResponse,
    PlanLatestResponse,
    TimestepPlan,
)

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

LONG_POLL_TIMEOUT = 60


@dataclass(slots=True)
class PlanPayload:
    response: PlanLatestResponse
    plan_dump: dict[str, Any]


class HassEnergyCoordinator(DataUpdateCoordinator[PlanPayload | None]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: HassEnergyApiClient,
        interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="hass_energy_plan",
            update_interval=interval,
        )
        self._client = client
        self._last_generated_at: str | None = None

    async def _async_update_data(self) -> PlanPayload | None:
        try:
            response, from_long_poll = await self._long_poll_for_plan()
        except (aiohttp.ClientError, ValueError) as exc:
            raise UpdateFailed(f"Failed to fetch EMS plan: {exc}") from exc
        if response is None:
            return None
        self._last_generated_at = response.plan.generated_at.isoformat()
        if from_long_poll:
            self.hass.async_create_task(self.async_request_refresh())
        return PlanPayload(
            response=response,
            plan_dump=response.plan.model_dump(mode="json"),
        )

    async def _long_poll_for_plan(self) -> tuple[PlanLatestResponse | None, bool]:
        """Use long-polling to wait for new plans, falling back to latest on timeout.

        Returns a tuple of (response, from_long_poll) where from_long_poll indicates
        whether the response came from a successful long-poll (vs timeout/fallback).
        """
        try:
            await_response: PlanAwaitResponse | None = await self._client.await_plan(
                since=self._last_generated_at,
                timeout=LONG_POLL_TIMEOUT,
            )
            if await_response is not None:
                return (
                    PlanLatestResponse(
                        run=await_response.run,
                        plan=await_response.plan,
                    ),
                    True,
                )
        except aiohttp.ClientError:
            _LOGGER.debug("Long-poll failed, falling back to get_latest_plan")

        return (await self._client.get_latest_plan(), False)


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
