"""Validate that ems_plan.json baselines in fixture directories stay in sync."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from hass_energy.config import load_app_config
from hass_energy.ems.fixture_harness import (
    EmsFixturePaths,
    resolve_ems_fixture_paths,
    summarize_plan,
)
from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.lib.source_resolver.fixtures import (
    FixtureHassDataProvider,
    freeze_hass_source_time,
)
from hass_energy.lib.source_resolver.resolver import ValueResolverImpl

FIXTURE_BASE = Path("tests/fixtures/ems")


def _scenario_from_env() -> str | None:
    raw = os.getenv("EMS_SCENARIO")
    if raw is None:
        return None
    name = raw.strip()
    return name or None


def _is_complete_bundle(paths: EmsFixturePaths) -> bool:
    return bool(
        paths.fixture_path.exists()
        and paths.config_path.exists()
        and paths.plan_path.exists()
    )


def _discover_fixture_scenarios() -> list[str]:
    """Find all fixture bundles with a baseline plan."""
    if not FIXTURE_BASE.exists():
        return []
    scenario_env = _scenario_from_env()
    if scenario_env:
        return [scenario_env]

    scenarios: list[str] = []
    for child in FIXTURE_BASE.iterdir():
        if not child.is_dir():
            continue
        paths = resolve_ems_fixture_paths(FIXTURE_BASE, child.name)
        if _is_complete_bundle(paths):
            scenarios.append(child.name)
    return sorted(scenarios)


@pytest.mark.parametrize("scenario", _discover_fixture_scenarios())
def test_fixture_baseline_up_to_date(scenario: str) -> None:
    """Re-solve each fixture and assert it matches the stored ems_plan.json."""
    paths = resolve_ems_fixture_paths(FIXTURE_BASE, scenario)
    if not _is_complete_bundle(paths):
        pytest.skip("EMS fixture scenario not recorded.")

    app_config = load_app_config(paths.config_path)
    provider, captured_at = FixtureHassDataProvider.from_path(paths.fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None

    with freeze_hass_source_time(now):
        resolver = ValueResolverImpl(hass_data_provider=provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()
        plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(now=now)

    actual = summarize_plan(plan)
    expected = json.loads(paths.plan_path.read_text())

    record_hint = f"hass-energy ems record-scenario --name {scenario}"
    assert actual == expected, (
        f"Fixture {scenario!r} ems_plan.json is out of date. "
        "Re-record with: " + record_hint
    )
