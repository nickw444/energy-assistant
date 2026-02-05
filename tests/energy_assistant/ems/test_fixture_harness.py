from __future__ import annotations

from pathlib import Path

from energy_assistant.ems.fixture_harness import resolve_ems_fixture_paths


def test_resolve_ems_fixture_paths_fixture_only(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    fixture_dir = base_dir / "my-fixture"
    fixture_dir.mkdir(parents=True)

    paths = resolve_ems_fixture_paths(base_dir, "my-fixture")

    assert paths.fixture_dir == fixture_dir
    assert paths.scenario_dir == fixture_dir
    assert paths.fixture_path == fixture_dir / "ems_fixture.json"
    assert paths.config_path == fixture_dir / "ems_config.yaml"
    assert paths.plan_path == fixture_dir / "ems_plan.json"


def test_resolve_ems_fixture_paths_fixture_with_scenario(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    fixture_dir = base_dir / "my-fixture"
    scenario_dir = fixture_dir / "scenario-a"
    scenario_dir.mkdir(parents=True)

    paths = resolve_ems_fixture_paths(base_dir, "my-fixture", "scenario-a")

    assert paths.fixture_dir == fixture_dir
    assert paths.scenario_dir == scenario_dir
    assert paths.fixture_path == scenario_dir / "ems_fixture.json"
    assert paths.config_path == fixture_dir / "ems_config.yaml"
    assert paths.plan_path == scenario_dir / "ems_plan.json"


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
    assert paths_a.config_path == fixture_dir / "ems_config.yaml"
    assert paths_a.fixture_path == scenario_a / "ems_fixture.json"
    assert paths_b.fixture_path == scenario_b / "ems_fixture.json"
