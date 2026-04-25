from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from energy_assistant.ems.models import EmsPlanOutput, EmsPlanTimings
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.lib.home_assistant import HomeAssistantStateDict
from energy_assistant.lib.home_assistant_ws import HomeAssistantWebSocketClient
from energy_assistant.models.config import AppConfig
from energy_assistant.worker.service import (
    PRICE_DEBOUNCE_SECONDS,
    PlanRunState,
    RunTrigger,
    Worker,
)


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


class _StubPlanner:
    def __init__(self) -> None:
        self.mark_for_hydration_calls = 0
        self.hydrate_all_calls = 0
        self.generate_ems_run_calls = 0
        self.plan: EmsPlanOutput | None = None

    def mark_for_hydration(self) -> None:
        self.mark_for_hydration_calls += 1

    def hydrate_all(self) -> None:
        self.hydrate_all_calls += 1

    def generate_ems_run(self) -> object:
        self.generate_ems_run_calls += 1
        if self.plan is None:
            raise AssertionError("Test planner missing plan")
        return SimpleNamespace(plan=self.plan)


def _plan() -> EmsPlanOutput:
    return EmsPlanOutput(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        status="Optimal",
        objective_value=0.0,
        timings=EmsPlanTimings(
            build_seconds=0.0,
            solve_seconds=0.0,
            total_seconds=0.0,
        ),
        components={},
    )


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
    def planner(self) -> _StubPlanner:
        return _StubPlanner()

    async def test_debounce_coalesces_multiple_calls(
        self, app_config: AppConfig, planner: _StubPlanner
    ) -> None:
        ws_client = _StubWsClient()
        worker = Worker(
            planner=cast(EmsMilpPlanner, planner),
            price_entity_ids={"sensor.price_import", "sensor.price_export"},
            ha_ws_client=ws_client,
        )
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
        self, app_config: AppConfig, planner: _StubPlanner
    ) -> None:
        ws_client = _StubWsClient()
        worker = Worker(
            planner=cast(EmsMilpPlanner, planner),
            price_entity_ids={"sensor.price_import", "sensor.price_export"},
            ha_ws_client=ws_client,
        )
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

    def test_worker_marks_planner_for_hydration_on_init(self, planner: _StubPlanner) -> None:
        worker = Worker(
            planner=cast(EmsMilpPlanner, planner),
            price_entity_ids={"sensor.price_import"},
            ha_ws_client=_StubWsClient(),
        )

        assert worker is not None
        assert planner.mark_for_hydration_calls == 1


class TestWorkerSerialization:
    async def test_price_change_waits_for_active_run_before_replanning(self) -> None:
        planner = _StubPlanner()
        planner.plan = _plan()
        ws_client = _StubWsClient()
        worker = Worker(
            planner=cast(EmsMilpPlanner, planner),
            price_entity_ids={"sensor.price_import"},
            ha_ws_client=ws_client,
        )
        worker.start(start_scheduler=False)

        started_first = threading.Event()
        started_second = threading.Event()
        release_first = threading.Event()
        release_second = threading.Event()
        solve_calls = 0

        def blocking_solve() -> EmsPlanOutput:
            nonlocal solve_calls
            solve_calls += 1
            if solve_calls == 1:
                started_first.set()
                release_first.wait()
            else:
                started_second.set()
                release_second.wait()
            if planner.plan is None:
                raise AssertionError("Test planner missing plan")
            return planner.plan

        worker._solve_once_blocking = blocking_solve  # type: ignore[method-assign]

        first_run, already_running = await worker.trigger_run(RunTrigger.MANUAL)
        assert not already_running
        await asyncio.wait_for(asyncio.to_thread(started_first.wait), timeout=5)

        active_run, already_running = await worker.trigger_run(RunTrigger.PRICE_CHANGE)
        assert already_running
        assert active_run.run_id == first_run.run_id
        assert solve_calls == 1
        assert not started_second.is_set()

        release_first.set()
        await asyncio.wait_for(asyncio.to_thread(started_second.wait), timeout=5)
        assert solve_calls == 2

        release_second.set()

        async def _latest_plan_ready() -> tuple[PlanRunState, EmsPlanOutput]:
            while True:
                latest = await worker.get_latest()
                if latest is not None:
                    return latest
                await asyncio.sleep(0.01)

        latest_run, latest_plan = await asyncio.wait_for(_latest_plan_ready(), timeout=5)
        assert latest_run.status == "completed"
        assert latest_plan == planner.plan

        worker.stop()
        await ws_client.close()
