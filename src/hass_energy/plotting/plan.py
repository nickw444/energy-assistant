from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Iterable


def plot_plan(
    plan: object,
    *,
    title: str = "Energy Plan",
    output: Path | None = None,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError("matplotlib is required to plot plans") from exc

    slots = _extract_slots(plan)
    if not slots:
        raise ValueError("Plan has no slots to plot.")

    local_tz = datetime.now().astimezone().tzinfo or UTC
    times: list[datetime] = []
    for slot in slots:
        slot_time = _parse_time(slot.get("start"), local_tz=local_tz)
        times.append(slot_time or datetime.now(tz=local_tz))

    grid_import = _series(slots, "grid_import_kw", "grid_import")
    grid_export = _series(slots, "grid_export_kw", "grid_export")
    grid_net = _series(slots, "grid_kw")
    pv_kw = _series(slots, "pv_kw")
    load_kw = _series(slots, "load_kw")

    plt.figure(figsize=(12, 6))
    if _has_any(grid_import):
        plt.plot(times, grid_import, label="grid_import_kw")
    if _has_any(grid_export):
        plt.plot(times, grid_export, label="grid_export_kw")
    if _has_any(grid_net):
        plt.plot(times, grid_net, label="grid_kw")
    if _has_any(pv_kw):
        plt.plot(times, pv_kw, label="pv_kw")
    if _has_any(load_kw):
        plt.plot(times, load_kw, label="load_kw")

    plt.title(title)
    plt.xlabel("Time")
    plt.ylabel("kW")
    plt.legend(loc="best")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    if output is not None:
        plt.savefig(output)
        return
    plt.show()


def _extract_slots(plan: object) -> list[dict[str, Any]]:
    if hasattr(plan, "slots"):
        slots = getattr(plan, "slots")
        if isinstance(slots, list):
            return [slot for slot in slots if isinstance(slot, dict)]

    if isinstance(plan, dict):
        if isinstance(plan.get("slots"), list):
            return [slot for slot in plan["slots"] if isinstance(slot, dict)]
        nested = plan.get("plan")
        if isinstance(nested, dict) and isinstance(nested.get("slots"), list):
            return [slot for slot in nested["slots"] if isinstance(slot, dict)]

    return []


def _parse_time(value: object, *, local_tz: tzinfo) -> datetime | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=local_tz)
        return parsed.astimezone(local_tz)
    return None


def _series(
    slots: list[dict[str, Any]],
    *keys: str,
    default: float = 0.0,
) -> list[float]:
    values: list[float] = []
    for slot in slots:
        value: float | None = None
        for key in keys:
            if key in slot:
                try:
                    value = float(slot[key])
                except (TypeError, ValueError):
                    value = None
                break
        values.append(value if value is not None else default)
    return values


def _has_any(values: Iterable[float]) -> bool:
    return any(abs(value) > 1e-9 for value in values)
