from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
from collections.abc import Awaitable, Callable, Coroutine
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
    _subscriptions: dict[int, Callable[[dict[str, Any]], Awaitable[None] | None]] = field(
        init=False, default_factory=dict
    )
    _pending: dict[int, asyncio.Future[dict[str, Any]]] = field(
        init=False, default_factory=dict
    )
    _receiver_task: asyncio.Task[None] | None = field(init=False, default=None)

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
            self._receiver_task = asyncio.create_task(self._receiver_loop())
            return version

    async def disconnect(self) -> None:
        """Close the websocket connection if open."""
        if self._receiver_task:
            self._receiver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receiver_task
            self._receiver_task = None

        self._subscriptions.clear()

        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
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

    async def _receiver_loop(self) -> None:
        if not self._websocket:
            return
        try:
            while True:
                raw = await self._websocket.recv()
                data = json.loads(raw)
                msg_id = data.get("id")

                if msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(data)
                    continue

                subscription_handler = self._subscriptions.get(msg_id)
                if subscription_handler:
                    change = data.get("event") or {}
                    result = subscription_handler(change)
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                    continue
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _request(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self._websocket:
            raise RuntimeError("Websocket is not connected")

        request_id = int(message.get("id") or self._next_id())
        message["id"] = request_id

        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        await self._send(message)
        return await fut

    async def ping(self) -> None:
        """Send ping/pong to verify connection is alive."""
        response = await self._request({"type": "ping"})
        if response.get("type") != "pong":
            raise ValueError("Expected pong after ping")

    async def subscribe_states(
        self,
        entity_ids: list[str],
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, None] | None],
    ) -> int:
        """Subscribe to state changes for specific entities."""
        response = await self._request(
            {
                "type": "subscribe_entities",
                "entity_ids": entity_ids,
            }
        )
        if response.get("success") is not True:
            raise ValueError(f"Failed to subscribe: {response}")

        subscription_id = response.get("id")
        if subscription_id is None:
            raise ValueError("Subscription response missing id")
        subscription_id_int = int(subscription_id)
        self._subscriptions[subscription_id_int] = handler
        return subscription_id_int

    async def get_states(self, entity_ids: list[str] | None = None) -> dict[str, Any]:
        """Fetch states for specified entity IDs (or all if None/empty)."""
        response = await self._request(
            {
                "type": "get_states",
            }
        )

        if response.get("type") != "result":
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
