from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from energy_assistant.lib.home_assistant import HomeAssistantStateDict
from energy_assistant.lib.home_assistant_ws import HomeAssistantWebSocketClient
from energy_assistant.lib.source_resolver.resolver import ValueResolver
from energy_assistant.lib.source_resolver.sources import EntitySource
from energy_assistant.models.config import AppConfig
from energy_assistant.worker.service import PRICE_DEBOUNCE_SECONDS, Worker


class _StubWsClient(HomeAssistantWebSocketClient):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[HomeAssistantStateDict | None] = asyncio.Queue()

    async def publish(self, state: HomeAssistantStateDict) -> None:
        await self._queue.put(state)

    async def close(self) -> None:
        await self._queue.put(None)

    async def subscribe_state_changes(
        self,
        entity_ids: set[str],
    ) -> AsyncIterator[HomeAssistantStateDict]:
        while True:
            state = await self._queue.get()
            if state is None:
                return
            if state["entity_id"] not in entity_ids:
                continue
            yield state


class _StubResolver:
    def mark_for_hydration(self, value: object) -> None:
        _ = value

    def hydrate_all(self) -> None:
        return None

    def hydrate_history(self) -> None:
        return None

    def hydrate_states(self) -> None:
        return None

    def resolve[Q, R](self, source: EntitySource[Q, R]) -> R:
        raise AssertionError(f"Unexpected resolve call: {source}")

    def mark(self, source: object) -> None:
        _ = source


def _app_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "server": {
                "host": "127.0.0.1",
                "port": 6070,
                "data_dir": "./data",
            },
            "homeassistant": {
                "base_url": "https://hass.example.com",
                "token": "test-token",
            },
            "inputs": {
                "grid_price_import": {
                    "type": "forecast",
                    "forecast": {
                        "type": "home_assistant",
                        "platform": "amber_express",
                        "entity": "sensor.price_import",
                    },
                    "realtime": {
                        "type": "home_assistant",
                        "entity": "sensor.price_import",
                    },
                },
                "grid_price_export": {
                    "type": "forecast",
                    "forecast": {
                        "type": "home_assistant",
                        "platform": "amber_express",
                        "entity": "sensor.price_export",
                    },
                    "realtime": {
                        "type": "home_assistant",
                        "entity": "sensor.price_export",
                    },
                },
                "base_load_power": {
                    "type": "forecast",
                    "forecast": {
                        "type": "home_assistant",
                        "platform": "historical_average",
                        "entity": "sensor.base_load",
                        "history_days": 1,
                        "interval_duration": 5,
                        "forecast_horizon_hours": 1,
                        "unit": "W",
                    },
                    "realtime": {
                        "type": "home_assistant",
                        "entity": "sensor.base_load_now",
                    },
                },
            },
            "plant": {
                "switchboard": {"type": "switchboard"},
                "grid": {
                    "type": "grid",
                    "connection": "switchboard",
                    "constraints": {"max_import_kw": 10.0, "max_export_kw": 10.0},
                    "price_import": {"source": "inputs.grid_price_import"},
                    "price_export": {"source": "inputs.grid_price_export"},
                },
                "base_load": {
                    "type": "load",
                    "connection": "switchboard",
                    "name": "Base Load",
                    "power": "inputs.base_load_power",
                },
            },
        }
    )


class TestWorkerDebounce:
    @pytest.fixture
    def app_config(self) -> AppConfig:
        return _app_config()

    @pytest.fixture
    def mock_resolver(self) -> ValueResolver:
        return _StubResolver()

    async def test_debounce_coalesces_multiple_calls(
        self, app_config: AppConfig, mock_resolver: ValueResolver
    ) -> None:
        ws_client = _StubWsClient()
        worker = Worker(app_config=app_config, resolver=mock_resolver, ha_ws_client=ws_client)
        worker.start(start_scheduler=False)
        trigger_count = 0

        async def trigger_run(*args: object, **kwargs: object):
            nonlocal trigger_count
            _ = args, kwargs
            trigger_count += 1
            return None

        worker.trigger_run = trigger_run  # type: ignore[method-assign]

        await ws_client.publish(
            HomeAssistantStateDict(
                entity_id="sensor.price_import",
                state="1.0",
                attributes={},
                last_changed="2026-01-07T03:30:00+00:00",
                last_reported="2026-01-07T03:30:00+00:00",
                last_updated="2026-01-07T03:30:00+00:00",
            )
        )
        await ws_client.publish(
            HomeAssistantStateDict(
                entity_id="sensor.price_export",
                state="1.0",
                attributes={},
                last_changed="2026-01-07T03:30:00+00:00",
                last_reported="2026-01-07T03:30:00+00:00",
                last_updated="2026-01-07T03:30:00+00:00",
            )
        )
        await ws_client.publish(
            HomeAssistantStateDict(
                entity_id="sensor.price_import",
                state="1.1",
                attributes={},
                last_changed="2026-01-07T03:30:01+00:00",
                last_reported="2026-01-07T03:30:01+00:00",
                last_updated="2026-01-07T03:30:01+00:00",
            )
        )

        await asyncio.sleep(PRICE_DEBOUNCE_SECONDS + 0.1)
        worker.stop()
        await ws_client.close()

        assert trigger_count == 1

    async def test_debounce_cancels_on_stop(
        self, app_config: AppConfig, mock_resolver: ValueResolver
    ) -> None:
        ws_client = _StubWsClient()
        worker = Worker(app_config=app_config, resolver=mock_resolver, ha_ws_client=ws_client)
        worker.start(start_scheduler=False)
        trigger_count = 0

        async def trigger_run(*args: object, **kwargs: object):
            nonlocal trigger_count
            _ = args, kwargs
            trigger_count += 1
            return None

        worker.trigger_run = trigger_run  # type: ignore[method-assign]

        await ws_client.publish(
            HomeAssistantStateDict(
                entity_id="sensor.price_import",
                state="1.0",
                attributes={},
                last_changed="2026-01-07T03:30:00+00:00",
                last_reported="2026-01-07T03:30:00+00:00",
                last_updated="2026-01-07T03:30:00+00:00",
            )
        )
        worker.stop()

        await asyncio.sleep(PRICE_DEBOUNCE_SECONDS + 0.1)
        await ws_client.close()

        assert trigger_count == 0
