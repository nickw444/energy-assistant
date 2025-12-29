from __future__ import annotations

import datetime as dt
import logging
from typing import Any, cast

import httpx
from pydantic import BaseModel, ConfigDict


logger = logging.getLogger(__name__)


class HomeAssistantConfig(BaseModel):
    base_url: str
    token: str
    verify_tls: bool = True

    model_config = ConfigDict(extra="forbid")


class HomeAssistantClient:
    """Tiny client for Home Assistant API interactions."""

    def __init__(self, *, config: HomeAssistantConfig, timeout_seconds: float = 10.0) -> None:
        self._config = config
        self._timeout = timeout_seconds

    def _build_headers(self, token: str | None) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers["Authorization"] = f"Bearer {token}"
        return headers

    def _format_datetime(self, value: dt.datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()

    def fetch_realtime_state(self) -> list[Any]:
        base_url = self._config.base_url.rstrip("/")
        if not base_url:
            logger.warning("Home Assistant base_url not configured; skipping realtime fetch")
            return []

        url = f"{base_url}/api/states"
        headers = self._build_headers(self._config.token)

        try:
            with httpx.Client(
                verify=self._config.verify_tls,
                timeout=self._timeout,
            ) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch realtime data from Home Assistant: %s", exc)
            return []

    def fetch_history(
        self,
        *,
        start_time: dt.datetime | None = None,
        end_time: dt.datetime | None = None,
        entity_id: str | None = None,
        minimal_response: bool = True,
        no_attributes: bool = True,
    ) -> list[Any]:
        base_url = self._config.base_url.rstrip("/")
        if not base_url:
            logger.warning("Home Assistant base_url not configured; skipping history fetch")
            return []

        if start_time is None:
            url = f"{base_url}/api/history/period"
        else:
            url = f"{base_url}/api/history/period/{self._format_datetime(start_time)}"
        headers = self._build_headers(self._config.token)
        params: dict[str, str] = {}
        if end_time is not None:
            params["end_time"] = self._format_datetime(end_time)
        if entity_id:
            params["filter_entity_id"] = entity_id
        if minimal_response:
            params["minimal_response"] = "1"
        if no_attributes:
            params["no_attributes"] = "1"
        try:
            with httpx.Client(
                verify=self._config.verify_tls,
                timeout=self._timeout,
            ) as client:
                response = client.get(url, headers=headers, params=params or None)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, list):
                    return cast(list[Any], payload)
                logger.warning(
                    "Unexpected history response type from Home Assistant: %s",
                    type(payload).__name__,
                )
                return []
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch history data from Home Assistant: %s", exc)
            return []

    def fetch_entity_history(
        self,
        *,
        entity_id: str,
        start_time: dt.datetime,
        end_time: dt.datetime | None = None,
        minimal_response: bool = True,
        no_attributes: bool = True,
    ) -> list[dict[str, Any]]:
        payload = self.fetch_history(
            start_time=start_time,
            end_time=end_time,
            entity_id=entity_id,
            minimal_response=minimal_response,
            no_attributes=no_attributes,
        )
        if not payload:
            return []
        if isinstance(payload[0], list):
            nested = cast(list[list[dict[str, Any]]], payload)
            if not nested:
                return []
            return list(nested[0])
        return cast(list[dict[str, Any]], payload)
