from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast
from unittest.mock import AsyncMock, create_autospec

import httpx
import yaml
from fastapi import Depends, FastAPI

from energy_assistant.api.dependencies import GlobalDependencies, get_config
from energy_assistant.api.server import create_app
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.planning.horizon import HorizonFactory
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.inputs.fixtures import load_fixture_input_provider
from energy_assistant.models.config import AppConfig
from energy_assistant.worker import PlanRunState, Worker


def _load_fixture_config(tmp_path: Path) -> AppConfig:
    fixture_path = Path("tests/fixtures/ems/nwhass/config.yaml")
    loaded_raw: Any = yaml.safe_load(fixture_path.read_text())
    assert isinstance(loaded_raw, dict)
    loaded = cast(dict[str, Any], loaded_raw)

    server_raw = loaded.get("server")
    if not isinstance(server_raw, dict):
        server: dict[str, Any] = {}
        loaded["server"] = server
    else:
        server = cast(dict[str, Any], server_raw)
    server["data_dir"] = str(tmp_path)

    return AppConfig.model_validate(loaded)


def _make_worker_mock() -> Worker:
    return create_autospec(Worker, instance=True, spec_set=True)


def _build_fixture_plan(config: AppConfig) -> object:
    fixture_dir = Path("tests/fixtures/ems/nwhass/short-horizon-low-pv")
    input_provider, captured_at = load_fixture_input_provider(path=fixture_dir / "input.json")
    now = datetime.fromisoformat(captured_at) if captured_at else None
    return EmsMilpPlanner(
        input_provider=input_provider,
        horizon_factory=HorizonFactory(
            timestep_minutes=config.ems.timestep_minutes,
            horizon_minutes=config.ems.horizon_minutes,
            high_res_timestep_minutes=config.ems.high_res_timestep_minutes,
            high_res_horizon_minutes=config.ems.high_res_horizon_minutes,
        ),
        input_applicator=EmsInputApplicator(
            input_configs=config.inputs,
            power_aligner=PowerForecastAligner(),
            price_aligner=PriceForecastAligner(),
        ),
        system=EmsSystemFactory.create().build(config),
    ).generate_ems_run(now=now).plan


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
    plan = _build_fixture_plan(config)
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
