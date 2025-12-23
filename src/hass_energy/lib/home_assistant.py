from __future__ import annotations

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

    def fetch_history(self) -> list[dict[str, Any]]:
        base_url = self._config.base_url.rstrip("/")
        if not base_url:
            logger.warning("Home Assistant base_url not configured; skipping history fetch")
            return []

        url = f"{base_url}/api/history/period"
        headers = self._build_headers(self._config.token)
        try:
            with httpx.Client(
                verify=self._config.verify_tls,
                timeout=self._timeout,
            ) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, list):
                    return cast(list[dict[str, Any]], payload)
                logger.warning(
                    "Unexpected history response type from Home Assistant: %s",
                    type(payload).__name__,
                )
                return []
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch history data from Home Assistant: %s", exc)
            return []
