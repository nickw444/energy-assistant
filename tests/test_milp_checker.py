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


def test_plan_checker_battery_soc_charges_to_avoid_export() -> None:
    config = EnergySystemConfig(forecast_window_hours=1)
    start = datetime(2025, 1, 1, 6, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)

    realtime_state = {
        "import_price_forecast": [_forecast_item(start, end, 0.3, "AUD/kWh")],
        "export_price_forecast": [_forecast_item(start, end, 0.1, "AUD/kWh")],
        "load_forecast": [_forecast_item(start, end, 1.0, "kW")],
        "pv_forecast": [_forecast_item(start, end, 2.0, "kW")],
        "grid_export_limit_kw": 0.0,
        "batteries": [
            {
                "name": "battery_main",
                "soc_init_kwh": 0.0,
                "soc_min_kwh": 0.0,
                "soc_max_kwh": 10.0,
                "soc_reserve_kwh": 0.0,
                "charge_efficiency": 1.0,
                "discharge_efficiency": 1.0,
                "charge_power_max_kw": 5.0,
                "discharge_power_max_kw": 5.0,
            }
        ],
    }

    plan = MilpPlanner().generate_plan(config, realtime_state, history=[])

    errors = validate_plan(plan)
    assert errors == []

    final_soc = plan["slots"][-1]["battery"]["battery_main"]["soc_kwh"]
    assert abs(final_soc - 0.95) < 1e-6


def test_plan_checker_price_limits_disable_import_export() -> None:
    config = EnergySystemConfig(forecast_window_hours=1)
    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)

    realtime_state = {
        "import_price_forecast": [_forecast_item(start, end, 0.75, "AUD/kWh")],
        "export_price_forecast": [_forecast_item(start, end, 0.1, "AUD/kWh")],
        "load_forecast": [_forecast_item(start, end, 0.0, "kW")],
        "pv_forecast": [_forecast_item(start, end, 0.0, "kW")],
    }

    plan = MilpPlanner().generate_plan(config, realtime_state, history=[])

    errors = validate_plan(plan)
    assert errors == []

    slot = plan["slots"][0]
    assert abs(slot["grid_import_kw"]) < 1e-6
    assert abs(slot["grid_export_kw"]) < 1e-6


def test_plan_checker_ev_value_charges_up_to_target() -> None:
    config = EnergySystemConfig(forecast_window_hours=1)
    start = datetime(2025, 1, 1, 3, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)

    realtime_state = {
        "import_price_forecast": [_forecast_item(start, end, 0.3, "AUD/kWh")],
        "export_price_forecast": [_forecast_item(start, end, 0.2, "AUD/kWh")],
        "load_forecast": [_forecast_item(start, end, 0.0, "kW")],
        "pv_forecast": [_forecast_item(start, end, 0.0, "kW")],
        "evs": [
            {
                "name": "ev_main",
                "max_power_kw": 2.0,
                "availability": [True] * 12,
                "target_energy_kwh": 1.0,
                "value_per_kwh": 1.0,
            }
        ],
    }

    plan = MilpPlanner().generate_plan(config, realtime_state, history=[])

    errors = validate_plan(plan)
    assert errors == []

    ev_energy = sum(
        slot["ev"]["ev_main"]["charge_kw"] * slot["duration_h"]
        for slot in plan["slots"]
    )
    assert abs(ev_energy - 1.0) < 1e-6
