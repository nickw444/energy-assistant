from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, TypedDict, cast

from hass_energy.config import load_app_config
from hass_energy.lib import HomeAssistantClient

ENTITY_IDS: dict[str, str] = {
    "pv_power": "sensor.hass_energy_pv_power_smoothed_1m",
    "load_power": "sensor.hass_energy_load_power_smoothed_1m",
    "grid_power": "sensor.hass_energy_grid_power_smoothed_1m",
    "battery_power": "sensor.hass_energy_battery_power_smoothed_1m",
    "battery_soc": "sensor.inverter_battery_soc",
    "ev_soc": "sensor.tessie_battery",
    "battery_soh_pct": "sensor.inverter_modbus_battery_soh",
    "pv_forecast_today": "sensor.solcast_pv_forecast_forecast_today",
    "pv_forecast_tomorrow": "sensor.solcast_pv_forecast_forecast_tomorrow",
    "pv_forecast_day_3": "sensor.solcast_pv_forecast_forecast_day_3",
    "pv_forecast_day_4": "sensor.solcast_pv_forecast_forecast_day_4",
    "price_import_forecast": "sensor.amber_general_forecast",
    "price_export_forecast": "sensor.amber_feed_in_forecast",
    "price_import_now": "sensor.amber_general_price",
    "price_export_now": "sensor.amber_feed_in_price",
    "demand_window_flag": "binary_sensor.amber_demand_window",
    "ev_connected_flag": "binary_sensor.tesla_wall_connector_vehicle_connected",
    "ev_charge_power": "sensor.tessie_charger_power",
}


class Forecast(TypedDict):
    start: str
    end: str
    value: float
    unit: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Home Assistant entities and map to planner realtime input.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to YAML config",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(argv)

    app_config = load_app_config(args.config)
    client = HomeAssistantClient()

    states_payload = client.fetch_realtime_state(app_config.energy)
    mapped = map_states_to_realtime(
        states_payload,
        forecast_window_hours=app_config.energy.forecast_window_hours,
    )
    print(json.dumps(mapped, indent=2))
    return 0


def map_states_to_realtime(states_payload: Any, *, forecast_window_hours: int) -> dict[str, Any]:
    """Map a Home Assistant /api/states payload into planner realtime input."""
    if not isinstance(states_payload, list):
        return {}

    local_tz = datetime.now().astimezone().tzinfo or UTC
    states_list = cast(list[Any], states_payload)

    by_entity: dict[str, dict[str, Any]] = {}
    for entry_obj in states_list:
        if not isinstance(entry_obj, dict):
            continue
        entry: dict[str, Any] = entry_obj  # type: ignore[assignment]
        entity_id_obj: Any = entry.get("entity_id")
        entity_id = entity_id_obj if isinstance(entity_id_obj, str) else None
        if not entity_id:
            continue
        by_entity[entity_id] = entry

    realtime: dict[str, Any] = {}

    def _get(name: str) -> dict[str, Any] | None:
        entity_id = ENTITY_IDS.get(name)
        return by_entity.get(entity_id) if entity_id else None

    # Prices
    price_import_now = _extract_float_state(_get("price_import_now"))
    price_export_now = _extract_float_state(_get("price_export_now"))
    price_import_forecast = _extract_price_forecasts(
        _get("price_import_forecast"),
        local_tz=local_tz,
    )
    price_export_forecast = _extract_price_forecasts(
        _get("price_export_forecast"),
        local_tz=local_tz,
    )

    if price_import_now is not None:
        realtime["import_price"] = price_import_now
    if price_import_forecast:
        realtime["import_price_forecast"] = price_import_forecast
    if price_export_now is not None:
        realtime["export_price"] = price_export_now
    if price_export_forecast:
        realtime["export_price_forecast"] = price_export_forecast

    # Power/energy metrics
    for key in [
        "pv_power",
        "load_power",
        "grid_power",
        "battery_power",
        "ev_charge_power",
    ]:
        value = _extract_float_state(_get(key))
        if value is not None:
            realtime[key] = value / 1000.0  # convert W -> kW

    for key in [
        "battery_soc",
        "ev_soc",
        "battery_soh_pct",
    ]:
        value = _extract_float_state(_get(key))
        if value is not None:
            realtime[key] = value

    pv_forecasts = _collect_pv_forecasts(
        by_entity,
        window_hours=forecast_window_hours,
        now=datetime.now(UTC),
        local_tz=local_tz,
    )
    if pv_forecasts:
        realtime["pv_forecast"] = pv_forecasts

    # Flags
    demand_flag = _extract_bool_state(_get("demand_window_flag"))
    ev_connected = _extract_bool_state(_get("ev_connected_flag"))
    if demand_flag is not None:
        realtime["demand_window"] = demand_flag
    if ev_connected is not None:
        realtime["ev_connected"] = ev_connected

    return realtime


def _extract_float_state(entry: dict[str, Any] | None) -> float | None:
    if not entry:
        return None
    try:
        state_value = entry.get("state")
        return float(state_value) if state_value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_bool_state(entry: dict[str, Any] | None) -> bool | None:
    if not entry:
        return None
    state_value = entry.get("state")
    if isinstance(state_value, bool):
        return state_value
    if isinstance(state_value, str):
        lowered = state_value.lower()
        if lowered in {"on", "true", "1"}:
            return True
        if lowered in {"off", "false", "0"}:
            return False
    return None


def _extract_price_forecasts(entry: dict[str, Any] | None, *, local_tz: tzinfo) -> list[Forecast]:
    if not entry:
        return []
    attributes: dict[str, Any] = entry.get("attributes") or {}
    forecasts_raw = attributes.get("forecasts")
    if not isinstance(forecasts_raw, list):
        return []
    forecast_items = cast(list[dict[str, Any]], forecasts_raw)
    cleaned: list[Forecast] = []
    for item in forecast_items:
        start_raw = item.get("start_time")
        end_raw = item.get("end_time")
        price = item.get("advanced_price_predicted")
        if price is None:
            continue
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            continue
        start_iso = _to_local_iso(start_raw, local_tz=local_tz)
        end_iso = _to_local_iso(end_raw, local_tz=local_tz)
        if start_iso is None or end_iso is None:
            continue
        cleaned.append(
            Forecast(
                start=start_iso,
                end=end_iso,
                value=price_value,
                unit="AUD/kWh",
            )
        )
    return cleaned


def _collect_pv_forecasts(
    by_entity: dict[str, dict[str, Any]],
    *,
    window_hours: int,
    now: datetime,
    local_tz: tzinfo,
) -> list[Forecast]:
    detailed = _extract_detailed_solcast_forecast(
        by_entity,
        window_hours=window_hours,
        now=now,
        local_tz=local_tz,
    )
    detailed.sort(key=lambda f: f["start"])
    return detailed


def _extract_detailed_solcast_forecast(
    by_entity: dict[str, dict[str, Any]],
    *,
    window_hours: int,
    now: datetime,
    local_tz: tzinfo,
) -> list[Forecast]:
    """Extract Solcast detailedForecast into a normalized forecast list."""
    candidates = [
        ENTITY_IDS.get("pv_forecast_today", ""),
        ENTITY_IDS.get("pv_forecast_tomorrow", ""),
        ENTITY_IDS.get("pv_forecast_day_3", ""),
        ENTITY_IDS.get("pv_forecast_day_4", ""),
    ]
    detailed_sets: list[tuple[list[dict[str, Any]], str]] = []
    for entity_id in candidates:
        if not entity_id:
            continue
        entry = by_entity.get(entity_id)
        if not entry:
            continue
        attributes: dict[str, Any] = entry.get("attributes") or {}
        detailed_raw = attributes.get("detailedForecast")
        if isinstance(detailed_raw, list):
            detailed = cast(list[dict[str, Any]], detailed_raw)
            # Solcast detailedForecast pv_estimate values are instantaneous power (kW).
            detailed_sets.append((detailed, "kW"))

    if not detailed_sets:
        return []

    forecasts: list[Forecast] = []

    cutoff = now + timedelta(hours=window_hours) if window_hours > 0 else None

    for detailed, unit in detailed_sets:
        step = _infer_step(detailed)
        for item in detailed:
            period_start_raw = item.get("period_start")
            value_raw = item.get("pv_estimate")
            if value_raw is None:
                continue
            try:
                value = float(value_raw)
            except (TypeError, ValueError):
                continue
            try:
                start_dt = datetime.fromisoformat(str(period_start_raw))
            except (TypeError, ValueError):
                continue
            if start_dt < now or (cutoff and start_dt > cutoff):
                continue
            end_dt = start_dt + step
            start_iso = _to_local_iso(start_dt, local_tz=local_tz)
            end_iso = _to_local_iso(end_dt, local_tz=local_tz)
            if start_iso is None or end_iso is None:
                continue
            forecasts.append(
                Forecast(
                    start=start_iso,
                    end=end_iso,
                    value=value,
                    unit=unit,
                )
            )
    return forecasts


def _infer_step(detailed: list[dict[str, Any]]) -> timedelta:
    if len(detailed) >= 2:
        first = detailed[0].get("period_start")
        second = detailed[1].get("period_start")
        try:
            first_dt = datetime.fromisoformat(str(first))
            second_dt = datetime.fromisoformat(str(second))
            delta = second_dt - first_dt
            if delta.total_seconds() > 0:
                return delta
        except (TypeError, ValueError):
            pass
    return timedelta(minutes=30)


def _to_local_iso(value: Any, *, local_tz: tzinfo | None = None) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    target_tz = local_tz or datetime.now().astimezone().tzinfo or UTC
    return dt.astimezone(target_tz).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
class PriceForecast(TypedDict):
    start: str
    end: str
    value: float
    unit: str
    descriptor: str | None
    spike_status: str | None
