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

        balance = (pv_kw - curt_kw + import_kw) - (load_kw + export_kw)
        if abs(balance) > atol:
            errors.append(f"slot {idx} power balance violated: {balance}")

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
