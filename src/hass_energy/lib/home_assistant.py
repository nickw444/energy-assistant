from __future__ import annotations

import datetime as dt
import logging
from typing import TypedDict, cast

import httpx
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class HomeAssistantStateDict(TypedDict):
    entity_id: str
    state: str | float | int | None
    attributes: dict[str, object]
    last_changed: str
    last_reported: str
    last_updated: str


class HomeAssistantHistoryStateDict(TypedDict, total=False):
    entity_id: str
    state: str | float | int | None
    last_changed: str
    last_reported: str
    last_updated: str


class HomeAssistantConfig(BaseModel):
    base_url: str
    token: str
    verify_tls: bool = True
    timeout_seconds: float = 30.0

    model_config = ConfigDict(extra="forbid")

    def websocket_url(self) -> str:
        """Return the WebSocket URL for this Home Assistant instance."""
        base = self.base_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + "/api/websocket"
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + "/api/websocket"
        return base + "/api/websocket"


class HomeAssistantClient:
    """Tiny client for Home Assistant API interactions."""

    def __init__(self, *, config: HomeAssistantConfig) -> None:
        self._config = config
        self._timeout = config.timeout_seconds

    def _build_headers(self, token: str | None) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers["Authorization"] = f"Bearer {token}"
        return headers

    def _format_datetime(self, value: dt.datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.isoformat()

    def fetch_realtime_state(self) -> list[HomeAssistantStateDict]:
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
                return cast(list[HomeAssistantStateDict], response.json())
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch realtime data from Home Assistant: %s", exc)
            raise

    def fetch_entity_history(
        self,
        *,
        entity_id: str,
        start_time: dt.datetime,
        end_time: dt.datetime | None = None,
        minimal_response: bool = True,
        no_attributes: bool = True,
    ) -> list[HomeAssistantHistoryStateDict]:
        base_url = self._config.base_url.rstrip("/")
        if not base_url:
            logger.warning("Home Assistant base_url not configured; skipping history fetch")
            return []

        url = f"{base_url}/api/history/period/{self._format_datetime(start_time)}"
        headers = self._build_headers(self._config.token)
        params: dict[str, str] = {"filter_entity_id": entity_id}
        if end_time is not None:
            params["end_time"] = self._format_datetime(end_time)
        if minimal_response:
            params["minimal_response"] = "1"
        if no_attributes:
            params["no_attributes"] = "1"
        try:
            with httpx.Client(
                verify=self._config.verify_tls,
                timeout=self._timeout,
            ) as client:
                response = client.get(url, headers=headers, params=params)
                response.raise_for_status()
                payload: list[list[HomeAssistantHistoryStateDict]] = response.json()
                if not payload or not payload[0]:
                    return []
                return payload[0]
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch history data from Home Assistant: %s", exc)
            raise
