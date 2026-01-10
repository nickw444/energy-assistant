"""Async client for the HASS Energy API."""

from __future__ import annotations

from typing import Any, TypeVar

import aiohttp
import async_timeout
from pydantic import BaseModel, ValidationError

from .models import EmsConfig, PlanAwaitResponse, PlanLatestResponse, PlanRunResponse

T = TypeVar("T", bound=BaseModel)


class HassEnergyApiClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, timeout: int) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def get_latest_plan(self) -> PlanLatestResponse | None:
        status, payload = await self._request_json("GET", "/plan/latest")
        if status == 404:
            return None
        return _parse_payload(PlanLatestResponse, payload, "latest plan")

    async def run_plan(self) -> PlanRunResponse:
        _, payload = await self._request_json("POST", "/plan/run")
        return _parse_payload(PlanRunResponse, payload, "run plan")

    async def await_plan(
        self,
        *,
        since: str | None = None,
        timeout: int | None = None,
    ) -> PlanAwaitResponse | None:
        params: dict[str, Any] = {}
        if since is not None:
            params["since"] = since
        if timeout is not None:
            params["timeout"] = timeout
        request_timeout = (timeout or 30) + 10
        status, payload = await self._request_json(
            "GET", "/plan/await", params=params or None, request_timeout=request_timeout
        )
        if status == 204 or payload is None:
            return None
        return _parse_payload(PlanAwaitResponse, payload, "await plan")

    async def get_settings(self) -> EmsConfig:
        _, payload = await self._request_json("GET", "/settings")
        return _parse_payload(EmsConfig, payload, "settings")

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        request_timeout: int | None = None,
    ) -> tuple[int, Any]:
        url = f"{self._base_url}{path}"
        async with async_timeout.timeout(request_timeout or self._timeout):
            async with self._session.request(method, url, params=params) as resp:
                if resp.status in (204, 404):
                    return resp.status, None
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
                return resp.status, payload


def _parse_payload[T: BaseModel](model: type[T], payload: Any, label: str) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid {label} response") from exc
