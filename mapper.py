"""Custom hass-energy mapper mirroring the legacy optimiser mapping."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from hass_energy.mapper import HassEnergyMapper


@dataclass
class DemandWindow:
    days: list[str]
    start: str
    end: str

    @property
    def start_time(self) -> time:
        hour, minute = (int(part) for part in self.start.split(":", maxsplit=1))
        return time(hour=hour, minute=minute)

    @property
    def end_time(self) -> time:
        hour, minute = (int(part) for part in self.end.split(":", maxsplit=1))
        return time(hour=hour, minute=minute)


@dataclass
class DemandWindowSchedule:
    timezone: str
    windows: list[DemandWindow]


DEFAULT_DEMAND_SCHEDULE = DemandWindowSchedule(
    timezone="Australia/Sydney",
    windows=[
        DemandWindow(
            days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            start="15:00",
            end="21:00",
        )
    ],
)


class Mapper(HassEnergyMapper):
    """Mapper that mirrors legacy optimiser expectations."""

    def __init__(
        self,
        pv_power: str = "sensor.inverter_pv_total_power",
        load_power: str = "sensor.inverter_load_power",
        grid_power: str = "sensor.inverter_grid_meter_power",
        battery_power: str = "sensor.inverter_battery_power",
        battery_soc: str = "sensor.inverter_battery_soc",
        ev_soc: str = "sensor.tessie_battery",
        battery_soh_pct: str = "sensor.inverter_modbus_battery_soh",
        total_imported_energy: str = "sensor.inverter_modbus_grid_consumption_energy_total",
        total_exported_energy: str = "sensor.inverter_modbus_feed_in_energy_total",
        pv_yield_energy: str = "sensor.inverter_modbus_solar_energy_total",
        battery_charge_total: str = "sensor.inverter_modbus_battery_charge_total",
        battery_discharge_total: str = "sensor.inverter_modbus_battery_discharge_total",
        pv_forecast_today: str = "sensor.solcast_pv_forecast_forecast_today",
        pv_forecast_tomorrow: str = "sensor.solcast_pv_forecast_forecast_tomorrow",
        price_import_forecast: str = "sensor.amber_general_forecast",
        price_export_forecast: str = "sensor.amber_feed_in_forecast",
        price_import_now: str = "sensor.amber_general_price",
        price_export_now: str = "sensor.amber_feed_in_price",
        pv_power_smoothed_1m: str = "sensor.hass_energy_pv_power_smoothed_1m",
        pv_power_smoothed_5m: str = "sensor.hass_energy_pv_power_smoothed_5m",
        pv_power_smoothed_15m: str = "sensor.hass_energy_pv_power_smoothed_15m",
        battery_power_smoothed_1m: str = "sensor.hass_energy_battery_power_smoothed_1m",
        battery_power_smoothed_5m: str = "sensor.hass_energy_battery_power_smoothed_5m",
        battery_power_smoothed_15m: str = "sensor.hass_energy_battery_power_smoothed_15m",
        load_power_smoothed_1m: str = "sensor.hass_energy_load_power_smoothed_1m",
        load_power_smoothed_5m: str = "sensor.hass_energy_load_power_smoothed_5m",
        load_power_smoothed_15m: str = "sensor.hass_energy_load_power_smoothed_15m",
        grid_power_smoothed_1m: str = "sensor.hass_energy_grid_power_smoothed_1m",
        grid_power_smoothed_5m: str = "sensor.hass_energy_grid_power_smoothed_5m",
        grid_power_smoothed_15m: str = "sensor.hass_energy_grid_power_smoothed_15m",
        demand_window_flag: str = "binary_sensor.amber_demand_window",
        ev_connected_flag: str = "binary_sensor.tesla_wall_connector_vehicle_connected",
        ev_charge_power: str = "sensor.tessie_charger_power",
        demand_window_schedule: DemandWindowSchedule = DEFAULT_DEMAND_SCHEDULE,
    ) -> None:
        self.pv_power = pv_power
        self.load_power = load_power
        self.grid_power = grid_power
        self.battery_power = battery_power
        self.battery_soc = battery_soc
        self.ev_soc = ev_soc
        self.battery_soh_pct = battery_soh_pct
        self.total_imported_energy = total_imported_energy
        self.total_exported_energy = total_exported_energy
        self.pv_yield_energy = pv_yield_energy
        self.battery_charge_total = battery_charge_total
        self.battery_discharge_total = battery_discharge_total
        self.pv_forecast_today = pv_forecast_today
        self.pv_forecast_tomorrow = pv_forecast_tomorrow
        self.price_import_forecast = price_import_forecast
        self.price_export_forecast = price_export_forecast
        self.price_import_now = price_import_now
        self.price_export_now = price_export_now
        self.pv_power_smoothed_1m = pv_power_smoothed_1m
        self.pv_power_smoothed_5m = pv_power_smoothed_5m
        self.pv_power_smoothed_15m = pv_power_smoothed_15m
        self.battery_power_smoothed_1m = battery_power_smoothed_1m
        self.battery_power_smoothed_5m = battery_power_smoothed_5m
        self.battery_power_smoothed_15m = battery_power_smoothed_15m
        self.load_power_smoothed_1m = load_power_smoothed_1m
        self.load_power_smoothed_5m = load_power_smoothed_5m
        self.load_power_smoothed_15m = load_power_smoothed_15m
        self.grid_power_smoothed_1m = grid_power_smoothed_1m
        self.grid_power_smoothed_5m = grid_power_smoothed_5m
        self.grid_power_smoothed_15m = grid_power_smoothed_15m
        self.demand_window_flag = demand_window_flag
        self.ev_connected_flag = ev_connected_flag
        self.ev_charge_power = ev_charge_power
        self.demand_window_schedule = demand_window_schedule

    def required_entities(self) -> list[str]:
        ids = [
            self.pv_power,
            self.load_power,
            self.grid_power,
            self.battery_power,
            self.battery_soc,
            self.ev_soc,
            self.price_import_now,
            self.price_export_now,
            self.ev_connected_flag,
            self.ev_charge_power,
            self.battery_soh_pct,
            self.total_imported_energy,
            self.total_exported_energy,
            self.pv_yield_energy,
            self.battery_charge_total,
            self.battery_discharge_total,
            self.pv_power_smoothed_1m,
            self.pv_power_smoothed_5m,
            self.pv_power_smoothed_15m,
            self.battery_power_smoothed_1m,
            self.battery_power_smoothed_5m,
            self.battery_power_smoothed_15m,
            self.load_power_smoothed_1m,
            self.load_power_smoothed_5m,
            self.load_power_smoothed_15m,
            self.grid_power_smoothed_1m,
            self.grid_power_smoothed_5m,
            self.grid_power_smoothed_15m,
            self.pv_forecast_today,
            self.price_import_forecast,
            self.price_export_forecast,
            self.demand_window_flag,
            self.pv_forecast_tomorrow,
        ]
        return [eid for eid in ids if eid]

    def map(self, states: dict[str, Any]) -> dict[str, Any]:
        price_import_now = _float_state(states, self.price_import_now)
        price_export_now = _float_state(states, self.price_export_now)

        battery_soc_fraction = _float_state(states, self.battery_soc) / 100.0
        battery_soh_fraction = _float_state(states, self.battery_soh_pct) / 100.0
        nominal_capacity_kwh = 41.93
        ev_capacity_kwh = 75.8

        realtime = {
            "battery_soc": battery_soc_fraction,
            "battery_energy_kwh": battery_soc_fraction * battery_soh_fraction * nominal_capacity_kwh,
            "ev_soc": _float_state(states, self.ev_soc) / 100.0,
            "ev_energy_kwh": round((_float_state(states, self.ev_soc) / 100.0) * ev_capacity_kwh, 3),
            "pv_kw": _required_kw(states, self.pv_power),
            "load_kw": _required_kw(states, self.load_power),
            "grid_kw": _required_kw(states, self.grid_power),
            "battery_kw": _required_kw(states, self.battery_power),
            "pv_kw_smoothed_1m": _required_kw(states, self.pv_power_smoothed_1m),
            "pv_kw_smoothed_5m": _required_kw(states, self.pv_power_smoothed_5m),
            "pv_kw_smoothed_15m": _required_kw(states, self.pv_power_smoothed_15m),
            "battery_kw_smoothed_1m": _required_kw(states, self.battery_power_smoothed_1m),
            "battery_kw_smoothed_5m": _required_kw(states, self.battery_power_smoothed_5m),
            "battery_kw_smoothed_15m": _required_kw(states, self.battery_power_smoothed_15m),
            "load_kw_smoothed_1m": _required_kw(states, self.load_power_smoothed_1m),
            "load_kw_smoothed_5m": _required_kw(states, self.load_power_smoothed_5m),
            "load_kw_smoothed_15m": _required_kw(states, self.load_power_smoothed_15m),
            "grid_kw_smoothed_1m": _required_kw(states, self.grid_power_smoothed_1m),
            "grid_kw_smoothed_5m": _required_kw(states, self.grid_power_smoothed_5m),
            "grid_kw_smoothed_15m": _required_kw(states, self.grid_power_smoothed_15m),
            "demand_window_active": _demand_active(states, self.demand_window_flag),
            "ev_connected": _binary_state(states, self.ev_connected_flag, "EV connected flag entity is required"),
            "ev_charge_kw": _float_state(states, self.ev_charge_power),
            "price_import_cents": price_import_now * 100.0,
            "price_export_cents": price_export_now * 100.0,
        }

        meters = {
            "total_imported_energy_kwh": _float_state(states, self.total_imported_energy),
            "total_exported_energy_kwh": _float_state(states, self.total_exported_energy),
            "pv_yield_energy_kwh": _float_state(states, self.pv_yield_energy),
            "battery_charge_total_kwh": _float_state(states, self.battery_charge_total),
            "battery_discharge_total_kwh": _float_state(states, self.battery_discharge_total),
        }

        load_forecast_kw = realtime["load_kw_smoothed_5m"]

        forecast_windows = _build_forecast_windows(
            price_import_forecasts=_parse_amber_forecast(states, self.price_import_forecast),
            price_export_forecasts=_parse_amber_forecast(states, self.price_export_forecast),
            pv_forecasts_today=_parse_solcast_forecast(states, self.pv_forecast_today),
            pv_forecasts_tomorrow=_parse_solcast_forecast(states, self.pv_forecast_tomorrow),
            demand_schedule=self.demand_window_schedule,
            load_now_kw=load_forecast_kw,
        )

        return {"realtime": realtime, "horizon": {"windows": forecast_windows}, "meters": meters}


def _parse_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()


def _float_state(states: Dict[str, dict], eid: str) -> float:
    try:
        raw = states[eid]["state"]
        if str(raw).lower() in {"unknown", "unavailable", "none", "null"}:
            return 0.0
        return float(raw)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Entity {eid} has non-numeric state") from exc


def _demand_active(states: Dict[str, dict], flag: Optional[str]) -> bool:
    return _binary_state(states, flag, "Demand window flag entity is required")


def _binary_state(states: Dict[str, dict], eid: Optional[str], missing_msg: str) -> bool:
    if not eid:
        raise ValueError(missing_msg)
    if eid not in states:
        raise KeyError(f"Entity not found: {eid}")
    state = str(states[eid]["state"]).lower()
    return state in {"on", "true", "1", "active"}


def _required_kw(states: Dict[str, dict], eid: Optional[str]) -> float:
    if not eid:
        raise ValueError("Expected a power entity id, got None")
    return _float_state(states, eid) / 1000.0


def _parse_amber_forecast(states: Dict[str, dict], eid: str) -> list:
    if eid not in states:
        raise KeyError(f"Entity not found: {eid}")
    attrs = states[eid].get("attributes", {})
    forecasts = attrs.get("forecasts", [])
    if not forecasts:
        raise ValueError(f"Missing forecasts for {eid}")
    return forecasts


def _parse_solcast_forecast(states: Dict[str, dict], eid: Optional[str]) -> list:
    if not eid or eid not in states:
        return []
    attrs = states[eid].get("attributes", {})
    if attrs.get("detailedForecast"):
        return attrs["detailedForecast"]
    if attrs.get("detailedHourly"):
        return attrs["detailedHourly"]
    raise ValueError(f"Missing detailedForecast/detailedHourly for {eid}")


def _window_in_schedule(ts: datetime, schedule: DemandWindowSchedule) -> bool:
    if not schedule or not schedule.windows:
        return False
    if schedule.timezone:
        try:
            import zoneinfo

            tz = zoneinfo.ZoneInfo(schedule.timezone)
            ts_local = ts.astimezone(tz)
        except Exception:
            ts_local = ts
    else:
        ts_local = ts
    day = ts_local.strftime("%a")
    for window in schedule.windows:
        if window.days and day not in window.days:
            continue
        start_dt = ts_local.replace(
            hour=window.start_time.hour,
            minute=window.start_time.minute,
            second=0,
            microsecond=0,
        )
        end_dt = ts_local.replace(
            hour=window.end_time.hour,
            minute=window.end_time.minute,
            second=0,
            microsecond=0,
        )
        if start_dt <= ts_local < end_dt:
            return True
    return False


def _pv_kw_at(ts: datetime, pv_lookup: list[tuple[datetime, datetime, float]]) -> float:
    for start, end, kw in pv_lookup:
        if start <= ts < end:
            return kw
    return 0.0


def _build_forecast_windows(
    price_import_forecasts: List[dict],
    price_export_forecasts: List[dict],
    pv_forecasts_today: List[dict],
    pv_forecasts_tomorrow: List[dict],
    demand_schedule: DemandWindowSchedule,
    load_now_kw: float,
) -> List[dict[str, Any]]:
    """Align price and PV forecasts into 12h horizon windows."""

    if len(price_import_forecasts) < 34 or len(price_export_forecasts) < 34:
        raise ValueError("Need at least 34 price forecast windows for import/export")

    pv_lookup: list[tuple[datetime, datetime, float]] = []
    for entry in pv_forecasts_today + pv_forecasts_tomorrow:
        start = _parse_time(entry["period_start"])
        end = start + timedelta(minutes=30)
        pv_estimate_kwh = float(entry.get("pv_estimate", 0.0))
        pv_kw = pv_estimate_kwh * 2.0
        pv_lookup.append((start, end, pv_kw))

    windows: list[dict[str, Any]] = []
    count = min(len(price_import_forecasts), 34)
    for idx in range(count):
        imp = price_import_forecasts[idx]
        exp = price_export_forecasts[idx] if idx < len(price_export_forecasts) else imp
        start = _parse_time(imp["start_time"])
        end = _parse_time(imp["end_time"])
        seconds = (end - start).total_seconds()
        duration_minutes = max(1, int(math.ceil(seconds / 60.0)))
        duration_hours = duration_minutes / 60.0
        pv_kw = _pv_kw_at(start, pv_lookup)
        pv_energy_kwh = round(pv_kw * duration_hours, 3)
        load_energy_kwh = round(load_now_kw * duration_hours, 3)
        imp_per_kwh = imp["per_kwh"]
        exp_per_kwh = exp.get("per_kwh", imp_per_kwh)
        if imp_per_kwh in (None, "unavailable") or exp_per_kwh in (None, "unavailable"):
            raise ValueError(
                "Price forecast contains unavailable entries; cannot build forecast windows"
            )

        demand_active = _window_in_schedule(start, demand_schedule) if demand_schedule else False

        windows.append(
            {
                "start": start.isoformat(),
                "duration_minutes": duration_minutes,
                "pv_forecast_kwh": pv_energy_kwh,
                "load_forecast_kwh": load_energy_kwh,
                "price_import_cents": float(imp_per_kwh) * 100.0,
                "price_export_cents": float(exp_per_kwh) * 100.0,
                "demand_window_active": demand_active,
            }
        )

    return windows[:34]


def get_mapper() -> Mapper:
    """Factory for loader convenience."""
    return Mapper()
