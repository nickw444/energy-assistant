"""Data update coordinator for the Energy Assistant integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar, cast

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .energy_assistant_client import (
    BatteryComponentPlan,
    ComponentPlan,
    EmsPlanOutput,
    EmsSeriesPoint,
    EnergyAssistantApiClient,
    GridComponentPlan,
    InverterComponentPlan,
    LoadComponentPlan,
    LoadControlledEvComponentPlan,
    PlanAwaitResponse,
    PlanLatestResponse,
    PvComponentPlan,
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
    """Coordinator that uses continuous long-polling to fetch plan updates."""

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
        try:
            response = await self._client.get_latest_plan()
        except (aiohttp.ClientError, ValueError) as exc:
            raise UpdateFailed(f"Failed to fetch EMS plan: {exc}") from exc
        if response is None:
            return None
        self._last_generated_at = response.plan.generated_at.isoformat()
        return PlanPayload(
            response=response,
            plan_dump=response.plan.model_dump(mode="json"),
        )

    def start_long_poll_loop(self) -> None:
        if self._long_poll_task is not None and not self._long_poll_task.done():
            return
        self._long_poll_task = self.hass.async_create_background_task(  # pyright: ignore[reportAttributeAccessIssue]
            self._run_long_poll_loop(),
            name="energy_assistant_long_poll",
        )

    def stop_long_poll_loop(self) -> None:
        if self._long_poll_task is not None and not self._long_poll_task.done():
            self._long_poll_task.cancel()

    async def _run_long_poll_loop(self) -> None:
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
        await_response: PlanAwaitResponse | None = await self._client.await_plan(
            since=self._last_generated_at,
            timeout=LONG_POLL_TIMEOUT,
        )
        if await_response is None:
            return

        response = PlanLatestResponse(run=await_response.run, plan=await_response.plan)
        self._last_generated_at = response.plan.generated_at.isoformat()
        self.async_set_updated_data(
            PlanPayload(
                response=response,
                plan_dump=response.plan.model_dump(mode="json"),
            )
        )

def build_plan_series(
    points: list[EmsSeriesPoint],
    transform: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for point in points:
        value = point.value
        if transform is not None:
            value = transform(value)
        series.append(
            {
                "time": point.time.isoformat(),
                "value": value,
            }
        )
    return series


def component_series(component: ComponentPlan | None, attribute: str) -> list[EmsSeriesPoint]:
    if component is None:
        return []
    value = getattr(component, attribute, None)
    if not isinstance(value, list):
        return []
    values = cast(list[Any], value)
    points: list[EmsSeriesPoint] = []
    for point in values:
        if isinstance(point, EmsSeriesPoint):
            points.append(point)
    return points


def first_series_value(points: list[EmsSeriesPoint]) -> Any:
    if not points:
        return None
    return points[0].value


def component_value_getter(
    component_id: str,
    attribute: str,
) -> Callable[[PlanLatestResponse], Any]:
    def _get(response: PlanLatestResponse) -> Any:
        component = response.plan.components.get(component_id)
        return first_series_value(component_series(component, attribute))

    return _get


def component_series_getter(
    component_id: str,
    attribute: str,
) -> Callable[[PlanLatestResponse], list[EmsSeriesPoint]]:
    def _get(response: PlanLatestResponse) -> list[EmsSeriesPoint]:
        component = response.plan.components.get(component_id)
        return component_series(component, attribute)

    return _get


def components_of_type[T: ComponentPlan](
    plan: EmsPlanOutput,
    model: type[T],
) -> dict[str, T]:
    return {
        component_id: component
        for component_id, component in sorted(plan.components.items())
        if isinstance(component, model)
    }


def single_component[T: ComponentPlan](
    plan: EmsPlanOutput,
    model: type[T],
) -> tuple[str, T] | None:
    matches = components_of_type(plan, model)
    if not matches:
        return None
    component_id = next(iter(matches))
    return component_id, matches[component_id]


def plan_horizon_hours(plan: EmsPlanOutput) -> float | None:
    timestamps = [
        point.time
        for component in plan.components.values()
        for attribute in _component_series_attributes(component)
        for point in component_series(component, attribute)
    ]
    if len(timestamps) < 2:
        return None
    start = min(timestamps)
    end = max(timestamps)
    return (end - start).total_seconds() / 3600.0


def _component_series_attributes(component: ComponentPlan) -> list[str]:
    if isinstance(component, GridComponentPlan):
        return [
            "price_import_raw",
            "price_export_raw",
            "price_import_effective",
            "price_export_effective",
            "import_allowed",
            "import_kw",
            "export_kw",
            "net_kw",
        ]
    if isinstance(component, LoadComponentPlan):
        return ["power_kw"]
    if isinstance(component, InverterComponentPlan):
        return ["ac_net_kw"]
    if isinstance(component, PvComponentPlan):
        return ["available_kw", "actual_kw", "curtail_kw", "curtailment"]
    if isinstance(component, BatteryComponentPlan):
        return ["charge_kw", "discharge_kw", "soc_kwh", "soc_pct"]
    if isinstance(component, LoadControlledEvComponentPlan):
        return ["charge_kw", "soc_kwh", "soc_pct", "connected", "charge_allowed"]
    return []
