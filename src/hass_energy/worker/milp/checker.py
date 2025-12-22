from __future__ import annotations

from typing import Any


def validate_plan(plan: dict[str, Any], *, atol: float = 1e-6) -> list[str]:
    """Validate core MILP plan invariants; returns a list of human-readable errors."""
    errors: list[str] = []
    slots_raw = plan.get("slots")
    if not isinstance(slots_raw, list):
        return ["plan missing slots list"]
    if not slots_raw:
        return ["plan has no slots"]

    total_cost = _get_float(plan, "total_cost", errors, "plan")
    total_import_kwh = _get_float(plan, "total_import_kwh", errors, "plan")
    total_export_kwh = _get_float(plan, "total_export_kwh", errors, "plan")

    sum_cost = 0.0
    sum_import = 0.0
    sum_export = 0.0
    inverter_export_limit = plan.get("metadata", {}).get("inverter_export_limit_kw")
    import_price_cap = plan.get("metadata", {}).get("import_price_cap")
    export_price_floor = plan.get("metadata", {}).get("export_price_floor")
    try:
        inverter_export_limit_f = (
            float(inverter_export_limit)
            if inverter_export_limit is not None
            else None
        )
    except (TypeError, ValueError):
        errors.append("metadata invalid inverter_export_limit_kw")
        inverter_export_limit_f = None
    try:
        import_price_cap_f = float(import_price_cap) if import_price_cap is not None else None
    except (TypeError, ValueError):
        errors.append("metadata invalid import_price_cap")
        import_price_cap_f = None
    try:
        export_price_floor_f = (
            float(export_price_floor) if export_price_floor is not None else None
        )
    except (TypeError, ValueError):
        errors.append("metadata invalid export_price_floor")
        export_price_floor_f = None

    for idx, slot_any in enumerate(slots_raw):
        if not isinstance(slot_any, dict):
            errors.append(f"slot {idx} not a dict")
            continue
        slot = slot_any
        duration_h = _get_float(slot, "duration_h", errors, f"slot {idx}")
        pv_kw = _get_float(slot, "pv_kw", errors, f"slot {idx}")
        curt_kw = _get_float(slot, "pv_curtail_kw", errors, f"slot {idx}")
        load_kw = _get_float(slot, "load_kw", errors, f"slot {idx}")
        import_kw = _get_float(slot, "grid_import_kw", errors, f"slot {idx}")
        export_kw = _get_float(slot, "grid_export_kw", errors, f"slot {idx}")
        import_price = _get_float(slot, "import_price", errors, f"slot {idx}")
        export_price = _get_float(slot, "export_price", errors, f"slot {idx}")
        slot_cost = _get_float(slot, "slot_cost", errors, f"slot {idx}")
        battery_net, battery_discharge_kw = _battery_net_kw(slot.get("battery"), errors, idx)
        ev_load = _ev_load_kw(slot.get("ev"), errors, idx)

        if duration_h <= 0:
            errors.append(f"slot {idx} duration_h must be positive")

        if import_kw < -atol:
            errors.append(f"slot {idx} grid_import_kw negative: {import_kw}")
        if export_kw < -atol:
            errors.append(f"slot {idx} grid_export_kw negative: {export_kw}")
        if curt_kw < -atol:
            errors.append(f"slot {idx} pv_curtail_kw negative: {curt_kw}")
        if curt_kw - pv_kw > atol:
            errors.append(f"slot {idx} pv_curtail_kw exceeds pv_kw: {curt_kw} > {pv_kw}")
        if export_price >= 0 and curt_kw > atol:
            errors.append(
                f"slot {idx} curtailment with non-negative export price: {export_price}"
            )

        balance = (pv_kw - curt_kw + import_kw + battery_net) - (load_kw + export_kw + ev_load)
        if abs(balance) > atol:
            errors.append(f"slot {idx} power balance violated: {balance}")

        if inverter_export_limit_f is not None:
            inverter_throughput = pv_kw - curt_kw + battery_discharge_kw
            if inverter_throughput - inverter_export_limit_f > atol:
                errors.append(
                    f"slot {idx} inverter export limit exceeded: {inverter_throughput} > {inverter_export_limit_f}"
                )
        if import_price_cap_f is not None and import_price > import_price_cap_f + atol:
            if import_kw > atol:
                errors.append(
                    f"slot {idx} import above price cap: price {import_price}, import {import_kw}"
                )
        if export_price_floor_f is not None and export_price < export_price_floor_f - atol:
            if export_kw > atol:
                errors.append(
                    f"slot {idx} export below price floor: price {export_price}, export {export_kw}"
                )

        expected_cost = (import_price * import_kw - export_price * export_kw) * duration_h
        if abs(expected_cost - slot_cost) > atol:
            errors.append(
                f"slot {idx} slot_cost mismatch: expected {expected_cost}, got {slot_cost}"
            )

        sum_cost += slot_cost
        sum_import += import_kw * duration_h
        sum_export += export_kw * duration_h

    if abs(sum_cost - total_cost) > atol:
        errors.append(
            f"total_cost mismatch: expected {sum_cost}, got {total_cost}"
        )
    if abs(sum_import - total_import_kwh) > atol:
        errors.append(
            f"total_import_kwh mismatch: expected {sum_import}, got {total_import_kwh}"
        )
    if abs(sum_export - total_export_kwh) > atol:
        errors.append(
            f"total_export_kwh mismatch: expected {sum_export}, got {total_export_kwh}"
        )

    return errors


def _get_float(payload: dict[str, Any], key: str, errors: list[str], ctx: str) -> float:
    value = payload.get(key)
    if value is None:
        errors.append(f"{ctx} missing {key}")
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{ctx} invalid {key}: {value}")
        return 0.0


def _battery_net_kw(
    battery_payload: Any,
    errors: list[str],
    slot_idx: int,
) -> tuple[float, float]:
    if battery_payload is None:
        return 0.0, 0.0
    if not isinstance(battery_payload, dict):
        errors.append(f"slot {slot_idx} battery payload invalid")
        return 0.0, 0.0
    net = 0.0
    discharge_sum = 0.0
    for name, entry in battery_payload.items():
        if not isinstance(entry, dict):
            errors.append(f"slot {slot_idx} battery {name} entry invalid")
            continue
        try:
            power = float(entry.get("power_kw", 0.0))
        except (TypeError, ValueError):
            errors.append(f"slot {slot_idx} battery {name} invalid power_kw")
            continue
        net += power
        if power > 0:
            discharge_sum += power
    return net, discharge_sum


def _ev_load_kw(
    ev_payload: Any,
    errors: list[str],
    slot_idx: int,
) -> float:
    if ev_payload is None:
        return 0.0
    if not isinstance(ev_payload, dict):
        errors.append(f"slot {slot_idx} ev payload invalid")
        return 0.0
    load = 0.0
    for name, entry in ev_payload.items():
        if not isinstance(entry, dict):
            errors.append(f"slot {slot_idx} ev {name} entry invalid")
            continue
        try:
            power = float(entry.get("charge_kw", 0.0))
        except (TypeError, ValueError):
            errors.append(f"slot {slot_idx} ev {name} invalid charge_kw")
            continue
        if power < -1e-6:
            errors.append(f"slot {slot_idx} ev {name} negative charge_kw: {power}")
        load += power
    return load
