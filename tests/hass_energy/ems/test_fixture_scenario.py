from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from hass_energy.config import load_app_config
from hass_energy.ems.solver import solve_once
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

Summary = dict[str, object]


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _series_summary(values: list[float], *, sample: int = 6) -> Summary:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "sample": {"head": [], "tail": []}}
    mean = sum(values) / len(values)
    return {
        "min": _round(min(values)),
        "max": _round(max(values)),
        "mean": _round(mean),
        "sample": {
            "head": [_round(v) for v in values[:sample]],
            "tail": [_round(v) for v in values[-sample:]],
        },
    }


def _summarize_plan(plan: dict[str, object]) -> Summary:
    slots = [slot for slot in plan.get("slots", []) if isinstance(slot, dict)]
    durations = [float(slot.get("duration_h", 0.0)) for slot in slots]

    grid_import = [float(slot.get("grid_import_kw", 0.0)) for slot in slots]
    grid_export = [float(slot.get("grid_export_kw", 0.0)) for slot in slots]
    grid_net = [float(slot.get("grid_kw", 0.0)) for slot in slots]
    pv_kw = [float(slot.get("pv_kw", 0.0)) for slot in slots]
    load_kw = [float(slot.get("load_kw", 0.0)) for slot in slots]
    price_import = [float(slot.get("price_import", 0.0)) for slot in slots]
    price_export = [float(slot.get("price_export", 0.0)) for slot in slots]

    battery_charge: dict[str, list[float]] = {}
    battery_discharge: dict[str, list[float]] = {}
    battery_soc: dict[str, list[float]] = {}
    for slot in slots:
        for name, value in (slot.get("battery_charge_kw") or {}).items():
            battery_charge.setdefault(str(name), []).append(float(value))
        for name, value in (slot.get("battery_discharge_kw") or {}).items():
            battery_discharge.setdefault(str(name), []).append(float(value))
        for name, value in (slot.get("battery_soc_kwh") or {}).items():
            battery_soc.setdefault(str(name), []).append(float(value))

    batt_net_by_inv: dict[str, list[float]] = {}
    for name, discharge_series in battery_discharge.items():
        charge_series = battery_charge.get(name, [])
        batt_net_by_inv[name] = [
            discharge - charge
            for discharge, charge in zip(discharge_series, charge_series, strict=False)
        ]

    total_import_kwh = sum(
        imp * duration for imp, duration in zip(grid_import, durations, strict=False)
    )
    total_export_kwh = sum(
        exp * duration for exp, duration in zip(grid_export, durations, strict=False)
    )

    summary: Summary = {
        "status": plan.get("status"),
        "objective": _round(float(plan.get("objective", 0.0))),
        "totals": {
            "import_kwh": _round(total_import_kwh),
            "export_kwh": _round(total_export_kwh),
            "total_cost": _round(float(slots[-1].get("cumulative_cost", 0.0)) if slots else 0.0),
        },
        "series": {
            "grid_kw": _series_summary(grid_net),
            "grid_import_kw": _series_summary(grid_import),
            "grid_export_kw": _series_summary(grid_export),
            "pv_kw": _series_summary(pv_kw),
            "load_kw": _series_summary(load_kw),
            "price_import": _series_summary(price_import),
            "price_export": _series_summary(price_export),
        },
        "battery": {
            name: {
                "net_kw": _series_summary(series),
                "soc_kwh": _series_summary(battery_soc.get(name, [])),
            }
            for name, series in batt_net_by_inv.items()
        },
    }
    return summary


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
    resolver.hydrate()

    plan = solve_once(app_config, resolver=resolver, now=now)
    summary = _summarize_plan(plan)
    assert snapshot == summary
