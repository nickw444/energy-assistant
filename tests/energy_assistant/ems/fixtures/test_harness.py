from __future__ import annotations

import json
from pathlib import Path

from energy_assistant.ems.fixtures.harness import render_fixture_json, resolve_ems_fixture_paths


def test_resolve_ems_fixture_paths_fixture_only(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    fixture_dir = base_dir / "my-fixture"
    fixture_dir.mkdir(parents=True)

    paths = resolve_ems_fixture_paths(base_dir, "my-fixture")

    assert paths.fixture_dir == fixture_dir
    assert paths.scenario_dir == fixture_dir
    assert paths.fixture_path == fixture_dir / "input.json"
    assert paths.config_path == fixture_dir / "config.yaml"
    assert paths.plan_path == fixture_dir / "output.json"
    assert paths.plot_path == fixture_dir / "output.svg"


def test_resolve_ems_fixture_paths_fixture_with_scenario(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    fixture_dir = base_dir / "my-fixture"
    scenario_dir = fixture_dir / "scenario-a"
    scenario_dir.mkdir(parents=True)

    paths = resolve_ems_fixture_paths(base_dir, "my-fixture", "scenario-a")

    assert paths.fixture_dir == fixture_dir
    assert paths.scenario_dir == scenario_dir
    assert paths.fixture_path == scenario_dir / "input.json"
    assert paths.config_path == fixture_dir / "config.yaml"
    assert paths.plan_path == scenario_dir / "output.json"
    assert paths.plot_path == scenario_dir / "output.svg"


def test_resolve_ems_fixture_paths_config_at_fixture_level(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    fixture_dir = base_dir / "shared-config"
    scenario_a = fixture_dir / "a"
    scenario_b = fixture_dir / "b"
    scenario_a.mkdir(parents=True)
    scenario_b.mkdir(parents=True)

    paths_a = resolve_ems_fixture_paths(base_dir, "shared-config", "a")
    paths_b = resolve_ems_fixture_paths(base_dir, "shared-config", "b")

    assert paths_a.config_path == paths_b.config_path
    assert paths_a.config_path == fixture_dir / "config.yaml"
    assert paths_a.fixture_path == scenario_a / "input.json"
    assert paths_b.fixture_path == scenario_b / "input.json"


def test_resolve_ems_fixture_paths_prefers_scenario_config(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    fixture_dir = base_dir / "scenario-config"
    scenario_dir = fixture_dir / "a"
    scenario_dir.mkdir(parents=True)
    (fixture_dir / "config.yaml").write_text("fixture: true\n")
    (scenario_dir / "config.yaml").write_text("scenario: true\n")

    paths = resolve_ems_fixture_paths(base_dir, "scenario-config", "a")

    assert paths.fixture_config_path == fixture_dir / "config.yaml"
    assert paths.scenario_config_path == scenario_dir / "config.yaml"
    assert paths.config_path == scenario_dir / "config.yaml"


def test_render_fixture_json_inlines_scalar_objects_inside_arrays() -> None:
    payload = {
        "components": {
            "base_load": {
                "power_kw": [
                    {"time": "2026-01-14T20:50:00+11:00", "value": 0.525},
                    {"time": "2026-01-14T20:55:00+11:00", "value": 0.646},
                ]
            }
        }
    }

    rendered = render_fixture_json(payload)

    assert '"power_kw": [' in rendered
    assert '    {"time": "2026-01-14T20:50:00+11:00", "value": 0.525},' in rendered
    assert '    {"time": "2026-01-14T20:55:00+11:00", "value": 0.646}' in rendered
    assert json.loads(rendered) == payload
