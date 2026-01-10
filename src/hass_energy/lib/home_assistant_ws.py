from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets import ConnectionClosedError
from websockets.asyncio.client import ClientConnection

from hass_energy.lib.home_assistant import HomeAssistantConfig, HomeAssistantStateDict

logger = logging.getLogger(__name__)


class HomeAssistantWebSocketClient:
    """Async WebSocket client for Home Assistant state change subscriptions."""

    def __init__(self, *, config: HomeAssistantConfig) -> None:
        self._config = config

    async def _connect(self) -> ClientConnection:
        url = self._config.websocket_url()
        ssl_context: ssl.SSLContext | None = None
        if url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            if not self._config.verify_tls:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

        ws = await websockets.connect(url, ssl=ssl_context)

        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected initial message from HA: {msg}")

        await ws.send(json.dumps({"type": "auth", "access_token": self._config.token}))

        msg = json.loads(await ws.recv())
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Failed to authenticate with HA websocket: {msg}")

        logger.debug("Authenticated with Home Assistant WebSocket")
        return ws

    async def subscribe_state_changes(
        self,
        entity_ids: set[str],
    ) -> AsyncIterator[HomeAssistantStateDict]:
        """Yield state dicts whenever any of the given entities changes.

        Handles reconnection with exponential backoff.
        """
        backoff = 1.0
        max_backoff = 30.0

        while True:
            try:
                ws = await self._connect()
                backoff = 1.0

                await ws.send(
                    json.dumps(
                        {
                            "id": 1,
                            "type": "subscribe_events",
                            "event_type": "state_changed",
                        }
                    )
                )

                async for raw_msg in ws:
                    msg: dict[str, Any] = json.loads(raw_msg)
                    if msg.get("type") != "event":
                        continue

                    event: dict[str, Any] = msg.get("event") or {}
                    data: dict[str, Any] = event.get("data") or {}
                    entity_id: str | None = data.get("entity_id")
                    if entity_id not in entity_ids:
                        continue

                    new_state: dict[str, Any] | None = data.get("new_state")
                    if not new_state:
                        continue

                    yield HomeAssistantStateDict(
                        entity_id=str(new_state.get("entity_id", "")),
                        state=new_state.get("state"),
                        attributes=dict(new_state.get("attributes") or {}),
                        last_changed=str(new_state.get("last_changed", "")),
                        last_reported=str(new_state.get("last_reported", "")),
                        last_updated=str(new_state.get("last_updated", "")),
                    )

            except ConnectionClosedError as exc:
                logger.warning(
                    "HA websocket disconnected (%s); reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                logger.warning(
                    "HA websocket connection error (%s); reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue
