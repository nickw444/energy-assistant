from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hass_energy.config import EnergySystemConfig
from hass_energy.worker.milp import MilpPlanner
from hass_energy.worker.milp.checker import validate_plan


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _forecast_item(start: datetime, end: datetime, value: float, unit: str) -> dict[str, object]:
    return {
        "start": _iso(start),
        "end": _iso(end),
        "value": value,
        "unit": unit,
    }


def test_plan_checker_basic_import() -> None:
    config = EnergySystemConfig(forecast_window_hours=1)
    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)

    realtime_state = {
        "import_price_forecast": [_forecast_item(start, end, 0.3, "AUD/kWh")],
        "export_price_forecast": [_forecast_item(start, end, 0.1, "AUD/kWh")],
        "load_forecast": [_forecast_item(start, end, 1.0, "kW")],
        "pv_forecast": [_forecast_item(start, end, 0.4, "kW")],
    }

    plan = MilpPlanner().generate_plan(config, realtime_state, history=[])

    errors = validate_plan(plan)
    assert errors == []

    slot = plan["slots"][0]
    assert abs(slot["grid_import_kw"] - 0.6) < 1e-6
    assert abs(slot["grid_export_kw"]) < 1e-6
    assert abs(slot["pv_curtail_kw"]) < 1e-6


def test_plan_checker_negative_export_price_curtails() -> None:
    config = EnergySystemConfig(forecast_window_hours=1)
    start = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)

    realtime_state = {
        "import_price_forecast": [_forecast_item(start, end, 0.2, "AUD/kWh")],
        "export_price_forecast": [_forecast_item(start, end, -0.05, "AUD/kWh")],
        "load_forecast": [_forecast_item(start, end, 1.0, "kW")],
        "pv_forecast": [_forecast_item(start, end, 2.5, "kW")],
    }

    plan = MilpPlanner().generate_plan(config, realtime_state, history=[])

    errors = validate_plan(plan)
    assert errors == []

    slot = plan["slots"][0]
    assert abs(slot["grid_import_kw"]) < 1e-6
    assert abs(slot["grid_export_kw"]) < 1e-6
    assert abs(slot["pv_curtail_kw"] - 1.5) < 1e-6
