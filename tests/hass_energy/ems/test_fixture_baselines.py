"""Validate that ems_plan.json baselines in fixture directories stay in sync."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from hass_energy.config import load_app_config
from hass_energy.ems.fixture_harness import resolve_ems_fixture_paths, serialize_plan
from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.lib.source_resolver.fixtures import (
    FixtureHassDataProvider,
    freeze_hass_source_time,
)
from hass_energy.lib.source_resolver.resolver import ValueResolverImpl

FIXTURE_BASE = Path("tests/fixtures/ems")


def _discover_fixture_scenarios() -> list[str]:
    """Find all subdirectories with a complete fixture bundle."""
    if not FIXTURE_BASE.exists():
        return []
    scenarios: list[str] = []
    for child in FIXTURE_BASE.iterdir():
        if not child.is_dir():
            continue
        paths = resolve_ems_fixture_paths(FIXTURE_BASE, child.name)
        if (
            paths.fixture_path.exists()
            and paths.config_path.exists()
            and paths.plan_path.exists()
        ):
            scenarios.append(child.name)
    return sorted(scenarios)


@pytest.mark.parametrize("scenario", _discover_fixture_scenarios())
def test_fixture_baseline_up_to_date(scenario: str) -> None:
    """Re-solve each fixture and assert it matches the stored ems_plan.json."""
    paths = resolve_ems_fixture_paths(FIXTURE_BASE, scenario)

    app_config = load_app_config(paths.config_path)
    provider, captured_at = FixtureHassDataProvider.from_path(paths.fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None

    with freeze_hass_source_time(now):
        resolver = ValueResolverImpl(hass_data_provider=provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()
        plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(now=now)

    actual = serialize_plan(plan, normalize_timings=True)
    expected = json.loads(paths.plan_path.read_text())

    if "timings" in expected:
        expected["timings"] = {k: 0.0 for k in expected["timings"]}

    assert actual == expected, (
        f"Fixture {scenario!r} ems_plan.json is out of date. "
        "Re-record with: hass-energy ems record-scenario --name " + scenario
    )
