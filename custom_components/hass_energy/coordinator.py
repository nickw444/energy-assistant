"""Data update coordinator for the HASS Energy integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, TypeVar

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .hass_energy_client import (
    EmsPlanOutput,
    HassEnergyApiClient,
    PlanLatestResponse,
    TimestepPlan,
)

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


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

    async def _async_update_data(self) -> PlanPayload | None:
        try:
            response = await self._client.get_latest_plan()
        except (aiohttp.ClientError, ValueError) as exc:
            raise UpdateFailed(f"Failed to fetch EMS plan: {exc}") from exc
        if response is None:
            return None
        return PlanPayload(
            response=response,
            plan_dump=response.plan.model_dump(mode="json"),
        )


def get_timestep0(plan: EmsPlanOutput) -> TimestepPlan | None:
    if not plan.timesteps:
        return None
    return plan.timesteps[0]


def sorted_items(values: dict[str, T]) -> list[tuple[str, T]]:
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
