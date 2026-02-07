from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, cast

import httpx
import yaml
from fastapi import Depends, FastAPI

from energy_assistant.api.dependencies import GlobalDependencies, get_config
from energy_assistant.api.server import create_app
from energy_assistant.models.config import AppConfig


def _load_fixture_config(tmp_path: Path) -> AppConfig:
    fixture_path = Path("tests/fixtures/ems/nwhass/ems_config.yaml")
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


def test_create_app_sets_global_dependencies(tmp_path: Path) -> None:
    config = _load_fixture_config(tmp_path)
    app = create_app(app_config=config, worker=None)

    assert hasattr(app.state, "dependencies")
    deps = app.state.dependencies
    assert isinstance(deps, GlobalDependencies)
    assert deps.config is config
    assert deps.worker is None


async def test_settings_uses_get_config_dependency(tmp_path: Path) -> None:
    config = _load_fixture_config(tmp_path)
    app = create_app(app_config=config, worker=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/settings")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["timestep_minutes"] == config.ems.timestep_minutes
    assert payload["min_horizon_minutes"] == config.ems.min_horizon_minutes


async def test_plan_run_returns_500_without_worker(tmp_path: Path) -> None:
    config = _load_fixture_config(tmp_path)
    app = create_app(app_config=config, worker=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/plan/run")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Worker not available"


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
