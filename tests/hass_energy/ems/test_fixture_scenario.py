from __future__ import annotations

import os
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


def test_fixture_scenario_snapshot(snapshot: object) -> None:
    scenario = os.getenv("EMS_SCENARIO")
    fixture_base = Path("tests/fixtures/ems")
    paths = resolve_ems_fixture_paths(fixture_base, scenario)
    if not paths.fixture_path.exists() or not paths.config_path.exists():
        pytest.skip("EMS fixture scenario not recorded.")

    app_config = load_app_config(paths.config_path)
    provider, captured_at = FixtureHassDataProvider.from_path(paths.fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None
    with freeze_hass_source_time(now):
        resolver = ValueResolverImpl(hass_data_provider=provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()
        plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(now=now)
    payload = serialize_plan(plan, normalize_timings=True)
    assert snapshot == payload
