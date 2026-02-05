"""Validate that ems_plan.json baselines in fixture directories stay in sync."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from energy_assistant.config import load_app_config
from energy_assistant.ems.fixture_harness import (
    EmsFixturePaths,
    compute_plan_hash,
    resolve_ems_fixture_paths,
    summarize_plan,
)
from energy_assistant.ems.planner import EmsMilpPlanner
from energy_assistant.lib.source_resolver.fixtures import (
    FixtureHassDataProvider,
    freeze_hass_source_time,
)
from energy_assistant.lib.source_resolver.resolver import ValueResolverImpl

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
        config_path = fixture_dir / "ems_config.yaml"
        if not config_path.exists():
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
    """Re-solve each fixture and assert it matches the stored ems_plan.json."""
    paths = resolve_ems_fixture_paths(FIXTURE_BASE, fixture, scenario)
    if not _is_complete_bundle(paths):
        pytest.skip("EMS fixture scenario not recorded.")

    app_config = load_app_config(paths.config_path)
    provider, captured_at = FixtureHassDataProvider.from_path(paths.fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None

    with freeze_hass_source_time(now):
        resolver = ValueResolverImpl(hass_data_provider=provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()
        plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(
            now=now,
        )

    actual = summarize_plan(plan)
    expected = json.loads(paths.plan_path.read_text())

    record_hint = f"energy-assistant ems refresh-baseline --fixture {fixture} --scenario {scenario}"
    assert actual == expected, (
        f"Fixture {fixture}/{scenario!r} ems_plan.json is out of date. "
        "Re-record with: " + record_hint
    )


@pytest.mark.parametrize(
    ("fixture", "scenario"),
    _discover_fixture_scenarios(),
    ids=[f"{f}/{s}" for f, s in _discover_fixture_scenarios()],
)
def test_fixture_plot_up_to_date(fixture: str, scenario: str) -> None:
    """Assert the stored ems_plan.jpeg matches the current plan hash."""
    paths = resolve_ems_fixture_paths(FIXTURE_BASE, fixture, scenario)
    if not _is_complete_bundle(paths):
        pytest.skip("EMS fixture scenario not recorded.")

    record_hint = f"energy-assistant ems refresh-baseline --fixture {fixture} --scenario {scenario}"

    if paths.hash_path.exists() and not paths.plot_path.exists():
        pytest.fail(
            f"Fixture {fixture}/{scenario!r} has ems_plan.hash without ems_plan.jpeg. "
            f"Re-record with: {record_hint}"
        )

    if paths.plot_path.exists() and not paths.hash_path.exists():
        pytest.fail(
            f"Fixture {fixture}/{scenario!r} has ems_plan.jpeg without ems_plan.hash. "
            f"Re-record with: {record_hint}"
        )

    if not paths.hash_path.exists():
        pytest.fail(
            f"Fixture {fixture}/{scenario!r} missing ems_plan.hash. "
            f"Re-record with: {record_hint}"
        )

    if not paths.plot_path.exists():
        pytest.fail(
            f"Fixture {fixture}/{scenario!r} missing ems_plan.jpeg. "
            f"Re-record with: {record_hint}"
        )

    stored_hash = paths.hash_path.read_text().strip()
    expected = json.loads(paths.plan_path.read_text())
    actual_hash = compute_plan_hash(expected)

    assert stored_hash == actual_hash, (
        f"Fixture {fixture}/{scenario!r} ems_plan.jpeg is out of date "
        f"(hash mismatch: stored={stored_hash}, expected={actual_hash}). "
        "Re-record with: " + record_hint
    )
