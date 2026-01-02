from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from hass_energy.config import load_app_config
from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.lib.source_resolver.fixtures import FixtureHassDataProvider
from hass_energy.lib.source_resolver.resolver import ValueResolver


def _freeze_hass_source_time(monkeypatch: pytest.MonkeyPatch, frozen: datetime | None) -> None:
    if frozen is None:
        return

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen
            if frozen.tzinfo is None:
                return frozen.replace(tzinfo=tz)
            return frozen.astimezone(tz)

    import hass_energy.lib.source_resolver.hass_source as hass_source

    monkeypatch.setattr(hass_source.datetime, "datetime", FrozenDateTime)


def test_fixture_scenario_snapshot(snapshot: object, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture_path = Path("tests/fixtures/ems/ems_fixture.json")
    config_path = Path("tests/fixtures/ems/ems_config.yaml")
    if not fixture_path.exists() or not config_path.exists():
        pytest.skip("EMS fixture scenario not recorded.")

    app_config = load_app_config(config_path)
    provider, captured_at = FixtureHassDataProvider.from_path(fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None
    _freeze_hass_source_time(monkeypatch, now)
    resolver = ValueResolver(hass_data_provider=provider)
    resolver.mark_for_hydration(app_config)
    resolver.hydrate_all()

    plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(now=now)
    payload = plan.model_dump(mode="json")
    payload["timings"] = {key: 0.0 for key in payload["timings"]}
    assert snapshot == payload
