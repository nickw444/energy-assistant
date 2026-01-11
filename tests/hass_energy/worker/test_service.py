from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from hass_energy.lib.home_assistant import HomeAssistantConfig, HomeAssistantStateDict
from hass_energy.lib.home_assistant_ws import HomeAssistantWebSocketClient
from hass_energy.models.config import AppConfig
from hass_energy.models.plant import GridConfig, PlantConfig
from hass_energy.worker.service import PRICE_DEBOUNCE_SECONDS, Worker


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


class TestWorkerDebounce:
    @pytest.fixture
    def mock_app_config(self) -> MagicMock:
        config = MagicMock(spec=AppConfig)
        config.homeassistant = HomeAssistantConfig(
            base_url="https://hass.example.com",
            token="test-token",
        )
        config.plant = MagicMock(spec=PlantConfig)
        config.plant.grid = MagicMock(spec=GridConfig)
        config.plant.grid.realtime_price_import = MagicMock()
        config.plant.grid.realtime_price_import.entity = "sensor.price_import"
        config.plant.grid.realtime_price_export = MagicMock()
        config.plant.grid.realtime_price_export.entity = "sensor.price_export"
        return config

    @pytest.fixture
    def mock_resolver(self) -> MagicMock:
        resolver = MagicMock()
        resolver.mark_for_hydration = MagicMock()
        return resolver

    async def test_debounce_coalesces_multiple_calls(
        self, mock_app_config: MagicMock, mock_resolver: MagicMock
    ) -> None:
        ws_client = _StubWsClient()
        worker = Worker(app_config=mock_app_config, resolver=mock_resolver, ha_ws_client=ws_client)
        worker.start(start_scheduler=False)
        trigger_mock = AsyncMock()
        worker.trigger_run = trigger_mock

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

        assert trigger_mock.call_count == 1

    async def test_debounce_cancels_on_stop(
        self, mock_app_config: MagicMock, mock_resolver: MagicMock
    ) -> None:
        ws_client = _StubWsClient()
        worker = Worker(app_config=mock_app_config, resolver=mock_resolver, ha_ws_client=ws_client)
        worker.start(start_scheduler=False)
        trigger_mock = AsyncMock()
        worker.trigger_run = trigger_mock

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

        assert trigger_mock.call_count == 0
