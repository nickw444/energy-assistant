from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from websockets.legacy.client import WebSocketClientProtocol, connect

logger = logging.getLogger(__name__)


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
    _subscription_specs: list[tuple[list[str], Callable[[dict[str, Any]], Awaitable[None] | None]]] = field(  # noqa: E501
        init=False, default_factory=list
    )
    _pending: dict[int, asyncio.Future[dict[str, Any]]] = field(
        init=False, default_factory=dict
    )
    _receiver_task: asyncio.Task[None] | None = field(init=False, default=None)
    _closing: bool = field(init=False, default=False)
    _conn_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    async def connect(self, timeout: float = 10.0) -> str:
        """Establish and authenticate a websocket connection."""
        self._closing = False
        async with self._conn_lock:
            if self._is_connected():
                logger.debug("connect called but websocket already connected")
                return self._ha_version or "already-connected"
            version = await self._open_connection(timeout=timeout, start_receiver=True)
            return version

    async def disconnect(self) -> None:
        """Close the websocket connection if open."""
        self._closing = True
        if self._receiver_task:
            self._receiver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receiver_task
            self._receiver_task = None

        self._subscriptions.clear()
        self._subscription_specs.clear()

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
        while not self._closing:
            try:
                if not self._websocket:
                    await asyncio.sleep(1.0)
                    continue

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
            except Exception as exc:
                if self._closing:
                    return
                logger.warning("Receiver loop error: %s; triggering reconnect", exc)
                await self._handle_connection_lost(exc)
                # Stop this receiver; a new one will be started after reconnect.
                return

    async def _handle_connection_lost(self, exc: Exception) -> None:
        self._set_pending_exception(exc)
        logger.info("Connection lost; scheduling reconnect: %s", exc)
        await self._close_websocket()
        await self._reconnect_with_backoff()

    def _set_pending_exception(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _request(self, message: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_connected()
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
        await self._ensure_connected()
        spec = (list(entity_ids), handler)
        subscription_id = await self._register_subscription(spec)
        self._subscription_specs.append(spec)
        return subscription_id

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

    async def _ensure_connected(self) -> None:
        if self._is_connected():
            return
        async with self._conn_lock:
            if self._is_connected():
                return
            await self._open_connection(start_receiver=True)

    async def _open_connection(self, timeout: float = 10.0, start_receiver: bool = True) -> str:
        ws_url = _build_websocket_url(self.base_url)
        ssl_context = _ssl_context(self.verify_ssl) if ws_url.startswith("wss://") else None

        async with asyncio.timeout(timeout):
            logger.info("Connecting to Home Assistant websocket at %s", ws_url)
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
            if start_receiver:
                self._receiver_task = asyncio.create_task(self._receiver_loop())
            logger.info("Websocket connected; Home Assistant version %s", version)
            return version

    async def _close_websocket(self) -> None:
        if self._websocket is not None:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

    async def _reconnect_with_backoff(self) -> None:
        delay = 1.0
        while not self._closing:
            try:
                async with self._conn_lock:
                    if self._is_connected():
                        logger.debug("Reconnect aborted; websocket already connected")
                        return
                    logger.info("Attempting reconnect with %.1fs backoff", delay)
                    await self._open_connection(start_receiver=True)
                    await self._resubscribe_all()
                    logger.info("Reconnected and resubscribed successfully")
                    return
            except Exception as exc:
                logger.warning("Reconnect attempt failed: %s", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _resubscribe_all(self) -> None:
        self._subscriptions.clear()
        for spec in self._subscription_specs:
            await self._register_subscription(spec)

    async def _register_subscription(
        self, spec: tuple[list[str], Callable[[dict[str, Any]], Awaitable[None] | None]]
    ) -> int:
        entity_ids, handler = spec
        logger.debug("Registering subscription for %s", entity_ids)
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

    def _is_connected(self) -> bool:
        return self._websocket is not None and not self._websocket.closed
