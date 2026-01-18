from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from hass_energy.ems.fixture_harness import resolve_ems_fixture_paths


def test_resolve_ems_fixture_paths_name_uses_base_dir(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    scenario_dir = base_dir / "scenario-a"
    scenario_dir.mkdir(parents=True)

    paths = resolve_ems_fixture_paths(base_dir, "scenario-a")

    assert paths.root_dir == scenario_dir


def test_resolve_ems_fixture_paths_absolute_path(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    base_dir.mkdir(parents=True)
    scenario_dir = tmp_path / "scenario-b"
    scenario_dir.mkdir(parents=True)

    paths = resolve_ems_fixture_paths(base_dir, str(scenario_dir))

    assert paths.root_dir == scenario_dir


def test_resolve_ems_fixture_paths_relative_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    base_dir = tmp_path / "fixtures"
    base_dir.mkdir(parents=True)
    scenario_dir = tmp_path / "scenario-c"
    scenario_dir.mkdir(parents=True)

    monkeypatch.chdir(tmp_path)
    rel_path = Path("scenario-c")

    paths = resolve_ems_fixture_paths(base_dir, str(rel_path))

    assert paths.root_dir == rel_path
    assert paths.root_dir.resolve() == scenario_dir.resolve()


def test_resolve_ems_fixture_paths_nested_name_defaults_to_base_dir(tmp_path: Path) -> None:
    base_dir = tmp_path / "fixtures"
    base_dir.mkdir(parents=True)

    paths = resolve_ems_fixture_paths(base_dir, "nested/scenario-d")

    assert paths.root_dir == base_dir / "nested/scenario-d"
