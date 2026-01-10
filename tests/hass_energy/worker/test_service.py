from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hass_energy.lib.home_assistant import HomeAssistantConfig
from hass_energy.models.config import AppConfig
from hass_energy.models.plant import GridConfig, PlantConfig


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
        from hass_energy.worker.service import _PRICE_DEBOUNCE_SECONDS, Worker

        worker = Worker(app_config=mock_app_config, resolver=mock_resolver)
        worker._loop = asyncio.get_running_loop()

        trigger_mock = AsyncMock()
        worker.trigger_run = trigger_mock

        worker._schedule_debounced_replan()
        worker._schedule_debounced_replan()
        worker._schedule_debounced_replan()

        await asyncio.sleep(_PRICE_DEBOUNCE_SECONDS + 0.1)

        assert trigger_mock.call_count == 1

    async def test_debounce_cancels_on_stop(
        self, mock_app_config: MagicMock, mock_resolver: MagicMock
    ) -> None:
        from hass_energy.worker.service import Worker

        worker = Worker(app_config=mock_app_config, resolver=mock_resolver)
        worker._loop = asyncio.get_running_loop()
        worker._schedule_task = asyncio.create_task(asyncio.sleep(100))

        trigger_mock = AsyncMock()
        worker.trigger_run = trigger_mock

        worker._schedule_debounced_replan()
        worker.stop()

        await asyncio.sleep(1.0)

        assert trigger_mock.call_count == 0
