from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from websockets.legacy.client import WebSocketClientProtocol, connect


def _build_websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme:
        raise ValueError("home_assistant.base_url must include a scheme (http/https/ws/wss)")

    scheme_map = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
    scheme = scheme_map.get(parsed.scheme)
    if scheme is None:
        raise ValueError(
            f"Unsupported scheme '{parsed.scheme}' for home_assistant.base_url; "
            "use http, https, ws, or wss."
        )

    path = parsed.path.rstrip("/") + "/api/websocket"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _ssl_context(verify_ssl: bool) -> ssl.SSLContext | bool:
    if verify_ssl:
        return True
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


@dataclass
class HomeAssistantWebSocketClient:
    base_url: str
    token: str
    verify_ssl: bool = True
    ws_max_size: int | None = None
    _websocket: WebSocketClientProtocol | None = field(init=False, default=None)
    _msg_id: int = field(init=False, default=0)
    _ha_version: str | None = field(init=False, default=None)

    async def connect(self, timeout: float = 10.0) -> str:
        """Establish and authenticate a websocket connection."""
        if self._websocket:
            return self._ha_version or "already-connected"

        ws_url = _build_websocket_url(self.base_url)
        ssl_context = _ssl_context(self.verify_ssl) if ws_url.startswith("wss://") else None

        async with asyncio.timeout(timeout):
            websocket = await connect(ws_url, ssl=ssl_context, max_size=self.ws_max_size)
            initial = await websocket.recv()
            initial_data = json.loads(initial)
            if initial_data.get("type") != "auth_required":
                await websocket.close()
                raise ValueError("Unexpected response from Home Assistant (expected auth_required)")

            await websocket.send(
                json.dumps(
                    {
                        "type": "auth",
                        "access_token": self.token,
                    }
                )
            )
            auth_reply_raw = await websocket.recv()
            auth_reply = json.loads(auth_reply_raw)
            if auth_reply.get("type") != "auth_ok":
                await websocket.close()
                raise PermissionError(
                    f"Authentication failed: {auth_reply.get('message') or 'unknown error'}"
                )

            self._websocket = websocket
            version = initial_data.get("ha_version") or "unknown"
            self._ha_version = version
            return version

    async def disconnect(self) -> None:
        """Close the websocket connection if open."""
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send(self, message: dict[str, Any]) -> None:
        if not self._websocket:
            raise RuntimeError("Websocket is not connected")
        await self._websocket.send(json.dumps(message))

    async def _recv_json(self) -> dict[str, Any]:
        if not self._websocket:
            raise RuntimeError("Websocket is not connected")
        raw = await self._websocket.recv()
        return json.loads(raw)

    async def ping(self) -> None:
        """Send ping/pong to verify connection is alive."""
        await self._send({"id": self._next_id(), "type": "ping"})
        response = await self._recv_json()
        if response.get("type") != "pong":
            raise ValueError("Expected pong after ping")

    async def subscribe_states(
        self,
        entity_ids: list[str],
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> int:
        """Subscribe to state changes for specific entities."""
        await self._send(
            {
                "id": self._next_id(),
                "type": "subscribe_entities",
                "entities": entity_ids,
            }
        )
        response = await self._recv_json()
        if response.get("success") is not True:
            raise ValueError(f"Failed to subscribe: {response}")

        subscription_id = response.get("id")
        if subscription_id is None:
            raise ValueError("Subscription response missing id")
        subscription_id_int = int(subscription_id)

        async def listener() -> None:
            while True:
                event = await self._recv_json()
                if event.get("id") != subscription_id_int:
                    continue
                change = event.get("event") or {}
                await handler(change)

        # Fire and forget listener
        asyncio.create_task(listener())
        return subscription_id_int

    async def get_states(self, entity_ids: list[str] | None = None) -> dict[str, Any]:
        """Fetch states for specified entity IDs (or all if None/empty)."""
        request_id = self._next_id()
        await self._send(
            {
                "id": request_id,
                "type": "get_states",
            }
        )

        response = await self._recv_json()
        if response.get("id") != request_id or response.get("type") != "result":
            raise ValueError(f"Unexpected response to get_states: {response}")
        if not response.get("success"):
            raise ValueError(f"get_states failed: {response}")

        states = response.get("result") or []
        if not entity_ids:
            return {state["entity_id"]: state for state in states if state.get("entity_id")}

        filtered = {
            state["entity_id"]: state
            for state in states
            if state.get("entity_id") in entity_ids
        }
        return filtered
