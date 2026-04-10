from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from energy_assistant.ems.models import (
    BatteryComponentPlan,
    EmsPlanOutput,
    EmsSeriesPoint,
    GridComponentPlan,
    InverterComponentPlan,
    LoadComponentPlan,
    LoadControlledEvComponentPlan,
    PvComponentPlan,
)


@dataclass(frozen=True, slots=True)
class EmsFixturePaths:
    fixture_dir: Path
    scenario_dir: Path
    fixture_path: Path
    fixture_config_path: Path
    scenario_config_path: Path
    config_path: Path
    plan_path: Path
    plot_path: Path
    hash_path: Path
    logical_graph_path: Path
    logical_graph_hash_path: Path
    topology_graph_path: Path
    topology_graph_hash_path: Path


def resolve_ems_fixture_paths(
    base_dir: Path, fixture: str, scenario: str | None = None
) -> EmsFixturePaths:
    fixture_dir = base_dir / fixture
    scenario_dir = fixture_dir if scenario is None else fixture_dir / scenario
    fixture_config_path = fixture_dir / "config.yaml"
    scenario_config_path = scenario_dir / "config.yaml"
    if scenario is not None and scenario_config_path.exists():
        config_path = scenario_config_path
    else:
        config_path = fixture_config_path
    return EmsFixturePaths(
        fixture_dir=fixture_dir,
        scenario_dir=scenario_dir,
        fixture_path=scenario_dir / "input.json",
        fixture_config_path=fixture_config_path,
        scenario_config_path=scenario_config_path,
        config_path=config_path,
        plan_path=scenario_dir / "output.json",
        plot_path=scenario_dir / "output.jpeg",
        hash_path=scenario_dir / "output.json.hash",
        logical_graph_path=scenario_dir / "logical-graph.svg",
        logical_graph_hash_path=scenario_dir / "logical-graph.svg.hash",
        topology_graph_path=scenario_dir / "topology-graph.svg",
        topology_graph_hash_path=scenario_dir / "topology-graph.svg.hash",
    )


def compute_plan_hash(plan_summary: dict[str, Any]) -> str:
    """Compute a stable hash from the plan summary for change detection."""
    normalized = dict(plan_summary)
    normalized.pop("generated_at", None)
    if "meta" in normalized:
        meta = dict(normalized["meta"])
        meta.pop("generated_at", None)
        normalized["meta"] = meta
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def compute_text_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def render_fixture_json(payload: dict[str, Any]) -> str:
    """Render fixture JSON with compact scalar objects inside arrays."""
    return _render_json_value(payload, indent=0, inline_scalar_object=False) + "\n"


def _render_json_value(value: Any, *, indent: int, inline_scalar_object: bool) -> str:
    if isinstance(value, Mapping):
        mapping = cast(dict[str, Any], value)
        if inline_scalar_object and _is_inline_scalar_object(mapping):
            return json.dumps(mapping, sort_keys=True)
        return _render_json_object(mapping, indent=indent)
    if isinstance(value, list):
        list_value = cast(list[Any], value)
        return _render_json_array(list_value, indent=indent)
    return json.dumps(value)


def _render_json_object(value: dict[str, Any], *, indent: int) -> str:
    if not value:
        return "{}"

    items = sorted(value.items())
    lines = ["{"]
    for index, (key, item) in enumerate(items):
        rendered = _render_json_value(item, indent=indent + 2, inline_scalar_object=False)
        rendered_lines = rendered.splitlines()
        suffix = "," if index < len(items) - 1 else ""
        lines.append(" " * (indent + 2) + f"{json.dumps(key)}: {rendered_lines[0]}")
        if len(rendered_lines) > 1:
            lines.extend(rendered_lines[1:-1])
            lines.append(rendered_lines[-1] + suffix)
        else:
            lines[-1] += suffix
    lines.append(" " * indent + "}")
    return "\n".join(lines)


def _render_json_array(value: list[Any], *, indent: int) -> str:
    if not value:
        return "[]"

    lines = ["["]
    for index, item in enumerate(value):
        rendered = _render_json_value(item, indent=indent + 2, inline_scalar_object=True)
        rendered_lines = rendered.splitlines()
        suffix = "," if index < len(value) - 1 else ""
        lines.append(" " * (indent + 2) + rendered_lines[0])
        if len(rendered_lines) > 1:
            lines.extend(rendered_lines[1:-1])
            lines.append(rendered_lines[-1] + suffix)
        else:
            lines[-1] += suffix
    lines.append(" " * indent + "]")
    return "\n".join(lines)


def _is_inline_scalar_object(value: dict[str, Any]) -> bool:
    return all(_is_json_scalar(item) for item in value.values())


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, Mapping):
        mapping = cast(dict[str, Any], value)
        return {key: _round_floats(item) for key, item in mapping.items()}
    if isinstance(value, list):
        list_value = cast(list[Any], value)
        return [_round_floats(item) for item in list_value]
    return value


def normalize_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    timings = payload.get("timings")
    if isinstance(timings, dict):
        timings_dict = cast(dict[str, object], timings)
        normalized = dict(payload)
        normalized["timings"] = {key: 0.0 for key in timings_dict}
        return normalized
    return payload


def summarize_plan(plan: EmsPlanOutput, *, bucket_minutes: int = 60) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    rounded_plan = EmsPlanOutput.model_validate(payload)
    return _summarize_plan(rounded_plan, bucket_minutes=bucket_minutes)


def _summarize_plan(plan: EmsPlanOutput, *, bucket_minutes: int) -> dict[str, Any]:
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive.")
    grid = _single_component(plan, GridComponentPlan, "grid")
    load = _single_component(plan, LoadComponentPlan, "load", optional=True)
    inverters = _components_of_type(plan, InverterComponentPlan)
    pvs = _components_of_type(plan, PvComponentPlan)
    batteries = _components_of_type(plan, BatteryComponentPlan)
    evs = _components_of_type(plan, LoadControlledEvComponentPlan)

    if grid is None or not grid.import_kw:
        summary: dict[str, Any] = {
            "meta": {
                "generated_at": plan.generated_at.isoformat(),
                "status": plan.status,
                "objective_value": plan.objective_value,
                "horizon_start": None,
                "horizon_end": None,
                "intervals": 0,
                "duration_minutes": 0.0,
                "bucket_minutes": bucket_minutes,
            },
            "totals": {},
            "prices": {},
            "inverters": {},
            "pvs": {},
            "batteries": {},
            "evs": {},
            "buckets": [],
        }
        return _round_floats(summary)

    interval_end_times = _interval_end_times(plan, grid.import_kw)
    horizon_start = grid.import_kw[0].time
    horizon_end = interval_end_times[-1]
    total_seconds = (horizon_end - horizon_start).total_seconds()
    total_minutes = total_seconds / 60.0
    interval_minutes = [
        (end - point.time).total_seconds() / 60.0
        for point, end in zip(grid.import_kw, interval_end_times, strict=True)
    ]

    grid_import = _float_series(grid.import_kw)
    grid_export = _float_series(grid.export_kw)
    grid_net = _float_series(grid.net_kw)
    load_base = _float_series(load.power_kw) if load is not None else [0.0] * len(grid_import)
    ev_charge = _aggregate_series({
        component_id: _float_series(component.charge_kw)
        for component_id, component in evs.items()
    })
    pv_actual = _aggregate_series({
        component_id: _float_series(component.actual_kw)
        for component_id, component in pvs.items()
    })
    battery_charge = _aggregate_series(
        {
            component_id: _float_series(component.charge_kw)
            for component_id, component in batteries.items()
        }
    )
    battery_discharge = _aggregate_series(
        {
            component_id: _float_series(component.discharge_kw)
            for component_id, component in batteries.items()
        }
    )

    summary = {
        "meta": {
            "generated_at": plan.generated_at.isoformat(),
            "status": plan.status,
            "objective_value": plan.objective_value,
            "horizon_start": horizon_start.isoformat(),
            "horizon_end": horizon_end.isoformat(),
            "intervals": len(grid.import_kw),
            "duration_minutes": total_minutes,
            "interval_minutes": {
                "min": min(interval_minutes),
                "max": max(interval_minutes),
                "avg": total_minutes / len(interval_minutes),
                "unique": sorted(set(interval_minutes)),
            },
            "bucket_minutes": bucket_minutes,
        },
        "totals": {
            "grid_import_kwh": _energy_kwh(grid.import_kw, interval_end_times),
            "grid_export_kwh": _energy_kwh(grid.export_kw, interval_end_times),
            "grid_net_kwh": _energy_kwh(grid.net_kw, interval_end_times),
            "grid_import_kw_max": max(grid_import),
            "grid_export_kw_max": max(grid_export),
            "grid_net_kw_max": max(grid_net),
            "grid_net_kw_min": min(grid_net),
            "load_base_kwh": (
                _energy_kwh(load.power_kw, interval_end_times) if load is not None else 0.0
            ),
            "load_total_kwh": _energy_values_kwh(
                [base + ev for base, ev in zip(load_base, ev_charge, strict=True)],
                grid.import_kw,
                interval_end_times,
            ),
            "ev_charge_kwh": _energy_values_kwh(ev_charge, grid.import_kw, interval_end_times),
            "pv_kwh": _energy_values_kwh(pv_actual, grid.import_kw, interval_end_times),
            "battery_charge_kwh": _energy_values_kwh(
                battery_charge, grid.import_kw, interval_end_times
            ),
            "battery_discharge_kwh": _energy_values_kwh(
                battery_discharge, grid.import_kw, interval_end_times
            ),
            "total_cost": float(plan.objective_value or 0.0),
        },
        "prices": {
            "import_min": min(_float_series(grid.price_import_raw)),
            "import_max": max(_float_series(grid.price_import_raw)),
            "import_avg": _weighted_average(grid.price_import_raw, interval_end_times),
            "export_min": min(_float_series(grid.price_export_raw)),
            "export_max": max(_float_series(grid.price_export_raw)),
            "export_avg": _weighted_average(grid.price_export_raw, interval_end_times),
        },
        "inverters": {
            component_id: {"ac_net_kwh": _energy_kwh(component.ac_net_kw, interval_end_times)}
            for component_id, component in inverters.items()
        },
        "pvs": {
            component_id: {
                "available_kwh": _energy_kwh(component.available_kw, interval_end_times),
                "actual_kwh": _energy_kwh(component.actual_kw, interval_end_times),
                "curtail_kwh": _energy_kwh(component.curtail_kw, interval_end_times),
            }
            for component_id, component in pvs.items()
        },
        "batteries": {
            component_id: {
                "charge_kwh": _energy_kwh(component.charge_kw, interval_end_times),
                "discharge_kwh": _energy_kwh(component.discharge_kw, interval_end_times),
                "soc_pct_min": min(_float_series(component.soc_pct)),
                "soc_pct_max": max(_float_series(component.soc_pct)),
                "soc_pct_end": float(component.soc_pct[-1].value),
            }
            for component_id, component in batteries.items()
        },
        "evs": {
            component_id: {
                "charge_kwh": _energy_kwh(component.charge_kw, interval_end_times),
                "soc_pct_min": min(_float_series(component.soc_pct)),
                "soc_pct_max": max(_float_series(component.soc_pct)),
                "soc_pct_end": float(component.soc_pct[-1].value),
            }
            for component_id, component in evs.items()
        },
        "buckets": [],
    }
    return _round_floats(summary)


def _single_component[T](
    plan: EmsPlanOutput,
    model: type[T],
    component_type: str,
    *,
    optional: bool = False,
) -> T | None:
    matches = [component for component in plan.components.values() if isinstance(component, model)]
    if not matches:
        if optional:
            return None
        raise ValueError(f"Plan is missing required {component_type!r} component.")
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {component_type!r} component.")
    return matches[0]


def _components_of_type[T](plan: EmsPlanOutput, model: type[T]) -> dict[str, T]:
    return {
        component_id: component
        for component_id, component in sorted(plan.components.items())
        if isinstance(component, model)
    }


def _float_series(points: list[EmsSeriesPoint]) -> list[float]:
    return [float(point.value) for point in points]


def _interval_end_times(
    plan: EmsPlanOutput,
    interval_points: list[EmsSeriesPoint],
) -> list[datetime]:
    starts = [point.time for point in interval_points]
    if len(starts) == 1:
        return [_plan_end_time(plan) or (starts[0] + timedelta(minutes=5))]
    end_times = starts[1:]
    final_end = _plan_end_time(plan)
    if final_end is None or final_end <= starts[-1]:
        final_end = starts[-1] + (starts[-1] - starts[-2])
    end_times.append(final_end)
    return end_times


def _plan_end_time(plan: EmsPlanOutput) -> datetime | None:
    state_times = [
        component.soc_pct[-1].time
        for component in plan.components.values()
        if isinstance(component, BatteryComponentPlan | LoadControlledEvComponentPlan)
        and component.soc_pct
    ]
    if state_times:
        return max(state_times)
    return None


def _energy_kwh(points: list[EmsSeriesPoint], end_times: list[datetime]) -> float:
    return _energy_values_kwh(_float_series(points), points, end_times)


def _energy_values_kwh(
    values: list[float],
    reference_points: list[EmsSeriesPoint],
    end_times: list[datetime],
) -> float:
    total = 0.0
    for value, point, end_time in zip(values, reference_points, end_times, strict=True):
        total += value * (end_time - point.time).total_seconds() / 3600.0
    return total


def _weighted_average(points: list[EmsSeriesPoint], end_times: list[datetime]) -> float:
    total_seconds = sum(
        (end_time - point.time).total_seconds()
        for point, end_time in zip(points, end_times, strict=True)
    )
    if total_seconds <= 0:
        return 0.0
    weighted = 0.0
    for point, end_time in zip(points, end_times, strict=True):
        weighted += float(point.value) * (end_time - point.time).total_seconds()
    return weighted / total_seconds


def _aggregate_series(series_dict: dict[str, list[float]]) -> list[float]:
    if not series_dict:
        return []
    total = [0.0] * len(next(iter(series_dict.values())))
    for series in series_dict.values():
        for index, value in enumerate(series):
            total[index] += value
    return total


def serialize_plan(plan: EmsPlanOutput, *, normalize_timings: bool = True) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    if normalize_timings:
        return normalize_plan_payload(payload)
    return payload
