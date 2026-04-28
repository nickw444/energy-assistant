"""Validate that output.json baselines in fixture directories stay in sync."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from energy_assistant.config import load_app_config
from energy_assistant.ems.fixtures.harness import (
    EmsFixturePaths,
    resolve_ems_fixture_paths,
    serialize_plan,
)
from energy_assistant.ems.horizon import HorizonFactory
from energy_assistant.ems.inputs.alignment import PowerForecastAligner, PriceForecastAligner
from energy_assistant.ems.inputs.application import EmsInputApplicator
from energy_assistant.ems.models import EmsPlanOutput
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.inputs.fixtures import load_fixture_input_provider
from energy_assistant.plotting import write_plan_svg

FIXTURE_BASE = Path("tests/fixtures/ems")


def _scenario_from_env() -> tuple[str, str] | None:
    raw = os.getenv("EMS_SCENARIO")
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    parts = value.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return (parts[0], parts[1])


def _is_complete_bundle(paths: EmsFixturePaths) -> bool:
    return bool(
        paths.fixture_path.exists()
        and paths.config_path.exists()
        and paths.plan_path.exists()
    )


def _discover_fixture_scenarios() -> list[tuple[str, str]]:
    """Find all fixture bundles with a baseline plan."""
    if not FIXTURE_BASE.exists():
        return []
    scenario_env = _scenario_from_env()
    if scenario_env:
        return [scenario_env]

    scenarios: list[tuple[str, str]] = []
    for fixture_dir in FIXTURE_BASE.iterdir():
        if not fixture_dir.is_dir():
            continue
        for scenario_dir in fixture_dir.iterdir():
            if not scenario_dir.is_dir():
                continue
            paths = resolve_ems_fixture_paths(FIXTURE_BASE, fixture_dir.name, scenario_dir.name)
            if _is_complete_bundle(paths):
                scenarios.append((fixture_dir.name, scenario_dir.name))
    return sorted(scenarios)


@pytest.mark.parametrize(
    ("fixture", "scenario"),
    _discover_fixture_scenarios(),
    ids=[f"{f}/{s}" for f, s in _discover_fixture_scenarios()],
)
def test_fixture_baseline_up_to_date(fixture: str, scenario: str) -> None:
    """Re-solve each fixture and assert it matches the stored output.json."""
    paths = resolve_ems_fixture_paths(FIXTURE_BASE, fixture, scenario)
    if not _is_complete_bundle(paths):
        pytest.skip("EMS fixture scenario not recorded.")

    app_config = load_app_config(paths.config_path)
    input_provider, captured_at = load_fixture_input_provider(path=paths.fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None

    plan = EmsMilpPlanner(
        input_provider=input_provider,
        horizon_factory=HorizonFactory(
            timestep_minutes=app_config.ems.timestep_minutes,
            horizon_minutes=app_config.ems.horizon_minutes,
            high_res_timestep_minutes=app_config.ems.high_res_timestep_minutes,
            high_res_horizon_minutes=app_config.ems.high_res_horizon_minutes,
        ),
        input_applicator=EmsInputApplicator(
            input_configs=app_config.inputs,
            power_aligner=PowerForecastAligner(),
            price_aligner=PriceForecastAligner(),
        ),
        system=EmsSystemFactory.create().build(app_config),
    ).generate_ems_run(now=now).plan

    actual = serialize_plan(plan)
    expected = json.loads(paths.plan_path.read_text())

    record_hint = f"energy-assistant ems refresh-baseline --fixture {fixture} --name {scenario}"
    assert actual == expected, (
        f"Fixture {fixture}/{scenario!r} output.json is out of date. "
        "Re-record with: " + record_hint
    )


@pytest.mark.parametrize(
    ("fixture", "scenario"),
    _discover_fixture_scenarios(),
    ids=[f"{f}/{s}" for f, s in _discover_fixture_scenarios()],
)
def test_fixture_plot_up_to_date(fixture: str, scenario: str) -> None:
    """Assert each fixture includes a static SVG plot artifact."""
    paths = resolve_ems_fixture_paths(FIXTURE_BASE, fixture, scenario)
    if not _is_complete_bundle(paths):
        pytest.skip("EMS fixture scenario not recorded.")

    record_hint = f"energy-assistant ems refresh-baseline --fixture {fixture} --name {scenario}"

    if not paths.plot_path.exists():
        pytest.fail(
            f"Fixture {fixture}/{scenario!r} missing output.svg. "
            f"Re-record with: {record_hint}"
        )

    content_start = paths.plot_path.read_text(encoding="utf-8", errors="ignore")[:200]
    assert "<?xml" in content_start or "<svg" in content_start


@pytest.mark.parametrize(
    ("fixture", "scenario"),
    _discover_fixture_scenarios(),
    ids=[f"{f}/{s}" for f, s in _discover_fixture_scenarios()],
)
def test_fixture_graph_artifacts_exist(fixture: str, scenario: str) -> None:
    """Assert each fixture includes logical and topological graph SVG artifacts."""
    paths = resolve_ems_fixture_paths(FIXTURE_BASE, fixture, scenario)
    if not _is_complete_bundle(paths):
        pytest.skip("EMS fixture scenario not recorded.")

    record_hint = f"energy-assistant ems refresh-baseline --fixture {fixture} --name {scenario}"
    graph_paths = [
        paths.logical_component_graph_path,
        paths.topological_energy_graph_path,
    ]
    for graph_path in graph_paths:
        if not graph_path.exists():
            pytest.fail(
                f"Fixture {fixture}/{scenario!r} missing {graph_path.name}. "
                f"Re-record with: {record_hint}"
            )
        content_start = graph_path.read_text(encoding="utf-8", errors="ignore")[:200]
        assert "<?xml" in content_start or "<svg" in content_start


def test_write_plan_svg_renders_fixture_plan(tmp_path: Path) -> None:
    plan = EmsPlanOutput.model_validate_json(
        (FIXTURE_BASE / "nwhass" / "short-horizon-low-pv" / "output.json").read_text()
    )
    output = tmp_path / "output.svg"

    write_plan_svg(plan, output)

    assert output.read_text().lstrip().startswith("<?xml")
    assert output.stat().st_size < 250_000
