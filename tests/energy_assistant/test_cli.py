from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from energy_assistant.cli import cli
from energy_assistant.inputs.registry import ResolvedForecastInput, ResolvedInputRegistry
from energy_assistant.inputs.window import InputWindow
from energy_assistant.models.inputs import InputValueKind


class _FakeInputProvider:
    def mark_for_hydration(self) -> None:
        return

    def hydrate_all(self) -> None:
        return

    def resolve_for_window(self, *, window: InputWindow) -> ResolvedInputRegistry:
        _ = window
        return ResolvedInputRegistry(
            forecasts={
                "base_load_power": ResolvedForecastInput(
                    key="base_load_power",
                    kind=InputValueKind.POWER,
                    points={"2025-01-01T00:00:00+00:00": 1.25},
                    interval_minutes=5,
                    realtime_value=1.5,
                    extension_points=None,
                    extension_interval_minutes=None,
                )
            }
        )

    def grid_price_watch_entity_ids(self) -> set[str]:
        return set()


class _FakeHassClient:
    def __init__(self, *, config: Any) -> None:
        _ = config


class _FakeHassDataProvider:
    def __init__(self, *, hass_client: Any) -> None:
        _ = hass_client


class _FakeResolver:
    def __init__(self, *, hass_data_provider: Any) -> None:
        _ = hass_data_provider


def _fake_input_provider_factory(*, app_config: Any, resolver: Any) -> _FakeInputProvider:
    _ = app_config
    _ = resolver
    return _FakeInputProvider()


def test_record_scenario_writes_resolved_inputs_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("energy_assistant.cli.HomeAssistantClient", _FakeHassClient)
    monkeypatch.setattr("energy_assistant.cli.HassDataProviderImpl", _FakeHassDataProvider)
    monkeypatch.setattr("energy_assistant.cli.ValueResolverImpl", _FakeResolver)
    monkeypatch.setattr(
        "energy_assistant.cli.ResolverBackedInputProvider",
        _fake_input_provider_factory,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--config",
            "tests/fixtures/ems/nwhass/config.yaml",
            "ems",
            "record-scenario",
            "--output-dir",
            str(tmp_path),
            "--fixture",
            "demo",
            "--name",
            "case-a",
            "--no-write-plan",
        ],
    )

    assert result.exit_code == 0, result.output

    payload = json.loads((tmp_path / "demo" / "case-a" / "input.json").read_text())
    assert "inputs" in payload
    assert "states" not in payload
    assert "history" not in payload
    assert payload["inputs"]["base_load_power"]["points"] == {
        "2025-01-01T00:00:00+00:00": 1.25
    }
