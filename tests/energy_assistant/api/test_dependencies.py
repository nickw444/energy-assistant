from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, create_autospec

import httpx
from fastapi import Depends, FastAPI

from energy_assistant.api.dependencies import GlobalDependencies, get_config
from energy_assistant.api.server import create_app
from energy_assistant.ems.models import EmsPlanOutput
from energy_assistant.models.config import AppConfig
from energy_assistant.worker import PlanRunState, Worker


def _load_fixture_config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "server": {
                "host": "127.0.0.1",
                "port": 6070,
                "data_dir": str(tmp_path),
            },
            "homeassistant": {
                "base_url": "http://example.invalid",
                "token": "fixture-token",
            },
            "ems": {
                "timestep_minutes": 30,
                "horizon_minutes": 720,
                "high_res_timestep_minutes": 5,
                "high_res_horizon_minutes": 120,
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
                        "forecast_horizon_hours": 24,
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


def _make_worker_mock() -> Worker:
    return create_autospec(Worker, instance=True, spec_set=True)


def _build_fixture_plan() -> EmsPlanOutput:
    return EmsPlanOutput.model_validate_json(
        Path("tests/fixtures/ems/nwhass/short-horizon-low-pv/output.json").read_text()
    )


def test_create_app_sets_global_dependencies(tmp_path: Path) -> None:
    config = _load_fixture_config(tmp_path)
    worker = _make_worker_mock()
    app = create_app(app_config=config, worker=worker)

    assert hasattr(app.state, "dependencies")
    deps = app.state.dependencies
    assert isinstance(deps, GlobalDependencies)
    assert deps.config is config
    assert deps.worker is worker


async def test_settings_uses_get_config_dependency(tmp_path: Path) -> None:
    config = _load_fixture_config(tmp_path)
    app = create_app(app_config=config, worker=_make_worker_mock())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/settings")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["timestep_minutes"] == config.ems.timestep_minutes
    assert payload["horizon_minutes"] == config.ems.horizon_minutes


async def test_plan_run_uses_injected_worker(tmp_path: Path) -> None:
    config = _load_fixture_config(tmp_path)
    worker = _make_worker_mock()
    run_state = PlanRunState(
        run_id="run-123",
        status="running",
        accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    worker.trigger_run = AsyncMock(return_value=(run_state, False))
    app = create_app(app_config=config, worker=worker)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/plan/run")

    assert resp.status_code == 202
    assert resp.json() == {
        "run": {
            "run_id": "run-123",
            "status": "running",
            "accepted_at": "2026-01-01T00:00:00Z",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": None,
            "message": None,
        },
        "already_running": False,
    }
    worker.trigger_run.assert_awaited_once_with()


async def test_plan_latest_returns_series_only_components(tmp_path: Path) -> None:
    config = _load_fixture_config(tmp_path)
    worker = _make_worker_mock()
    plan = _build_fixture_plan()
    run_state = PlanRunState(
        run_id="run-123",
        status="completed",
        accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
    )
    worker.get_latest = AsyncMock(return_value=(run_state, plan))
    app = create_app(app_config=config, worker=worker)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/plan/latest")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["plan"]["components"]["primary"]["type"] == "inverter"
    assert "intent" not in payload["plan"]["components"]["primary"]
    assert payload["plan"]["components"]["tessie"]["type"] == "load_controlled_ev"
    assert "intent" not in payload["plan"]["components"]["tessie"]


async def test_missing_global_dependencies_returns_500() -> None:
    app = FastAPI()

    @app.get("/needs-config")
    def _needs_config(
        config: Annotated[AppConfig, Depends(get_config)],
    ) -> dict[str, str]:
        _ = config
        return {"ok": "true"}

    _ = _needs_config

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/needs-config")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Global dependencies missing"
