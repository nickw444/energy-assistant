from __future__ import annotations

from datetime import datetime
from typing import Any

from hass_energy.optimizer import HassEnergyOptimizer


class MapperAlignedOptimizer(HassEnergyOptimizer):
    """
    Rule-based optimizer tuned for the custom mapper output in mapper.py.

    Decisions are limited to the modes listed in SCRATCH.md. This keeps the dispatcher
    simple: it can translate modes into inverter/EV actions later on.
    """

    def required_entities(self) -> list[str]:
        return []

    def decide(self, mapped: dict[str, Any], entities: dict[str, Any]) -> dict[str, Any]:
        realtime = mapped.get("realtime") or {}
        horizon = (mapped.get("horizon") or {}).get("windows") or []

        price_import_cents = _to_float(realtime.get("price_import_cents"))
        price_export_cents = _to_float(realtime.get("price_export_cents"))
        battery_soc = _to_float(realtime.get("battery_soc"))
        pv_kw = _to_float(realtime.get("pv_kw"))
        load_kw = _to_float(realtime.get("load_kw"))
        grid_kw = _to_float(realtime.get("grid_kw"))
        ev_connected = bool(realtime.get("ev_connected"))
        ev_charge_kw = _to_float(realtime.get("ev_charge_kw"))
        demand_window_active = bool(realtime.get("demand_window_active"))

        surplus_kw = None
        if pv_kw is not None and load_kw is not None:
            current_ev_kw = max(ev_charge_kw or 0.0, 0.0)
            surplus_kw = pv_kw - load_kw - current_ev_kw

        mode = "SELF_CONSUME"
        reason = "Default to self-consume unless a stronger signal appears."
        forecast_pv_surplus_today = _forecast_surplus_today(horizon)

        # Negative feed-in: avoid exporting at a loss.
        if price_export_cents is not None and price_export_cents < 0:
            mode = "SELF_CONSUME_CURTAIL"
            reason = "Export price is negative; curtail to avoid paid export."
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        # Use forecasted PV to top up the EV when the battery is already healthy.
        if (
            ev_connected
            and battery_soc is not None
            and battery_soc > 0.9
            and forecast_pv_surplus_today is not None
            and forecast_pv_surplus_today >= 5.0
        ):
            mode = "EV_FROM_PV"
            reason = (
                f"Battery healthy and ~{forecast_pv_surplus_today:.1f} kWh PV surplus expected today; "
                "charge EV from PV."
            )
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        # Sell/export rules: step down the price requirement as SoC increases.
        sell_steps = [
            (0.9, 20.0),
            (0.7, 25.0),
            (0.5, 30.0),
        ]
        if price_export_cents is not None and battery_soc is not None:
            for soc_threshold, price_threshold in sell_steps:
                if battery_soc > soc_threshold and price_export_cents >= price_threshold:
                    mode = "EXPORT_MAX"
                    reason = (
                        f"SoC above {int(soc_threshold * 100)}% and export price >= {price_threshold}c; sell energy."
                    )
                    return _decision(
                        reason,
                        mode,
                        price_import_cents,
                        price_export_cents,
                        battery_soc,
                        pv_kw,
                        load_kw,
                        grid_kw,
                        ev_connected,
                        ev_charge_kw,
                        surplus_kw,
                    )

        # Buy/charge rules for low SoC and cheap import prices.
        buy_steps = [
            (0.3, 8.0),
            (0.5, 6.0),
            (0.7, 4.0),
        ]
        if price_import_cents is not None and battery_soc is not None:
            for soc_limit, price_limit in buy_steps:
                if battery_soc < soc_limit and price_import_cents <= price_limit:
                    mode = "GRID_CHARGE_BATTERY"
                    reason = (
                        f"SoC below {int(soc_limit * 100)}% with import price <= {price_limit}c; buy energy to charge."
                    )
                    return _decision(
                        reason,
                        mode,
                        price_import_cents,
                        price_export_cents,
                        battery_soc,
                        pv_kw,
                        load_kw,
                        grid_kw,
                        ev_connected,
                        ev_charge_kw,
                        surplus_kw,
                    )

        # Demand window: never import from grid; export is allowed, but avoid negative exports.
        if demand_window_active:
            if price_export_cents is not None and price_export_cents < 0:
                mode = "SELF_CONSUME_CURTAIL"
                reason = "Demand window active and export price negative; curtail to avoid import/export."
                return _decision(
                    reason,
                    mode,
                    price_import_cents,
                    price_export_cents,
                    battery_soc,
                    pv_kw,
                    load_kw,
                    grid_kw,
                    ev_connected,
                    ev_charge_kw,
                    surplus_kw,
                )

            if ev_connected and surplus_kw is not None and surplus_kw > 1.0:
                mode = "EV_FROM_PV"
                reason = "Demand window active; use PV surplus for EV without grid import."
                return _decision(
                    reason,
                    mode,
                    price_import_cents,
                    price_export_cents,
                    battery_soc,
                    pv_kw,
                    load_kw,
                    grid_kw,
                    ev_connected,
                    ev_charge_kw,
                    surplus_kw,
                )

            if surplus_kw is not None and surplus_kw > 0:
                mode = "EXPORT_MAX"
                reason = "Demand window active; export surplus, avoid grid import."
            else:
                mode = "SELF_CONSUME_CURTAIL"
                reason = "Demand window active; curtail to avoid grid import."
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        if price_export_cents is not None and price_export_cents < 0:
            mode = "SELF_CONSUME_CURTAIL"
            reason = "Export price is negative; avoid exporting."
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        if price_import_cents is not None and price_import_cents <= 12:
            if ev_connected:
                mode = "GRID_CHARGE_EV_AND_BATTERY"
                reason = "Very low price with EV connected; charge battery and EV from grid."
            else:
                mode = "GRID_CHARGE_BATTERY"
                reason = "Very low price; charge battery from grid/PV."
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        if (
            price_export_cents is not None
            and price_export_cents >= 45
            and battery_soc is not None
            and battery_soc > 0.3
        ):
            mode = "EXPORT_MAX"
            reason = "High export price and usable battery; maximise export."
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        if ev_connected:
            if surplus_kw is not None and surplus_kw > 1.0:
                mode = "EV_FROM_PV"
                reason = "PV surplus available with EV connected; charge EV from PV."
                return _decision(
                    reason,
                    mode,
                    price_import_cents,
                    price_export_cents,
                    battery_soc,
                    pv_kw,
                    load_kw,
                    grid_kw,
                    ev_connected,
                    ev_charge_kw,
                    surplus_kw,
                )
            if (
                battery_soc is not None
                and battery_soc > 0.5
                and price_export_cents is not None
                and price_export_cents >= 30
            ):
                mode = "EV_FROM_BATTERY"
                reason = "EV connected and battery healthy; use battery/PV for EV charging."
                return _decision(
                    reason,
                    mode,
                    price_import_cents,
                    price_export_cents,
                    battery_soc,
                    pv_kw,
                    load_kw,
                    grid_kw,
                    ev_connected,
                    ev_charge_kw,
                    surplus_kw,
                )

        if (
            battery_soc is not None
            and battery_soc < 0.2
            and price_import_cents is not None
            and price_import_cents <= 20
        ):
            mode = "GRID_CHARGE_BATTERY"
            reason = "Battery low and price acceptable; charge from grid/PV."
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        if (
            grid_kw is not None
            and grid_kw > 0
            and price_export_cents is not None
            and price_export_cents < 5
        ):
            mode = "SELF_CONSUME_CURTAIL"
            reason = "Exporting into poor prices; curtail/export limit."
            return _decision(
                reason,
                mode,
                price_import_cents,
                price_export_cents,
                battery_soc,
                pv_kw,
                load_kw,
                grid_kw,
                ev_connected,
                ev_charge_kw,
                surplus_kw,
            )

        return _decision(
            reason,
            mode,
            price_import_cents,
            price_export_cents,
            battery_soc,
            pv_kw,
            load_kw,
            grid_kw,
            ev_connected,
            ev_charge_kw,
            surplus_kw,
        )


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decision(
    reason: str,
    mode: str,
    price_import_cents: float | None,
    price_export_cents: float | None,
    battery_soc: float | None,
    pv_kw: float | None,
    load_kw: float | None,
    grid_kw: float | None,
    ev_connected: bool,
    ev_charge_kw: float | None,
    surplus_kw: float | None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "reason": reason,
        "inputs": {
            "price_import_cents": price_import_cents,
            "price_export_cents": price_export_cents,
            "battery_soc": battery_soc,
            "pv_kw": pv_kw,
            "load_kw": load_kw,
            "grid_kw": grid_kw,
            "ev_connected": ev_connected,
            "ev_charge_kw": ev_charge_kw,
            "pv_surplus_kw": surplus_kw,
        },
        "knobs": {},
    }


def _forecast_surplus_today(windows: list[dict[str, Any]]) -> float | None:
    """Estimate PV surplus (pv minus load) remaining today from forecast windows."""

    if not windows:
        return None

    now = datetime.now().astimezone()
    surplus = 0.0
    considered = False

    for window in windows:
        start_raw = window.get("start")
        try:
            start = datetime.fromisoformat(start_raw)
        except Exception:
            continue

        if start.astimezone(now.tzinfo).date() != now.date():
            continue
        if start < now:
            continue

        pv_kwh = _to_float(window.get("pv_forecast_kwh"))
        load_kwh = _to_float(window.get("load_forecast_kwh")) or 0.0
        if pv_kwh is None:
            continue

        surplus += max(pv_kwh - load_kwh, 0.0)
        considered = True

    if not considered:
        return None
    return surplus


def get_optimizer() -> MapperAlignedOptimizer:
    return MapperAlignedOptimizer()
