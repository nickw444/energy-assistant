from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from hass_energy.ems.models import EmsPlanOutput


@dataclass(frozen=True, slots=True)
class EmsFixturePaths:
    fixture_dir: Path
    scenario_dir: Path
    fixture_path: Path
    config_path: Path
    plan_path: Path
    plot_path: Path
    hash_path: Path


def resolve_ems_fixture_paths(
    base_dir: Path, fixture: str, scenario: str | None = None
) -> EmsFixturePaths:
    fixture_dir = base_dir / fixture
    scenario_dir = fixture_dir if scenario is None else fixture_dir / scenario
    return EmsFixturePaths(
        fixture_dir=fixture_dir,
        scenario_dir=scenario_dir,
        fixture_path=scenario_dir / "ems_fixture.json",
        config_path=fixture_dir / "ems_config.yaml",
        plan_path=scenario_dir / "ems_plan.json",
        plot_path=scenario_dir / "ems_plan.jpeg",
        hash_path=scenario_dir / "ems_plan.hash",
    )


def compute_plan_hash(plan_summary: dict[str, Any]) -> str:
    """Compute a stable hash from the plan summary for change detection."""
    normalized = dict(plan_summary)
    if "meta" in normalized:
        meta = dict(normalized["meta"])
        meta.pop("generated_at", None)
        normalized["meta"] = meta
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


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


def _update_min_max(
    current_min: float | None,
    current_max: float | None,
    value: float | None,
) -> tuple[float | None, float | None]:
    if value is None:
        return current_min, current_max
    if current_min is None or value < current_min:
        current_min = value
    if current_max is None or value > current_max:
        current_max = value
    return current_min, current_max


def _update_max(current: float | None, value: float) -> float:
    if current is None or value > current:
        return value
    return current


def _update_min(current: float | None, value: float) -> float:
    if current is None or value < current:
        return value
    return current


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

    timesteps = plan.timesteps
    if not timesteps:
        summary: dict[str, Any] = {
            "meta": {
                "generated_at": plan.generated_at.isoformat(),
                "status": plan.status,
                "objective_value": plan.objective_value,
                "horizon_start": None,
                "horizon_end": None,
                "timesteps": 0,
                "duration_minutes": 0.0,
                "timestep_minutes": {"min": None, "max": None, "avg": None, "unique": []},
                "bucket_minutes": bucket_minutes,
            },
            "totals": {},
            "prices": {},
            "inverters": {},
            "evs": {},
            "buckets": [],
        }
        return _round_floats(summary)

    horizon_start = timesteps[0].start
    horizon_end = timesteps[-1].end
    total_seconds = sum(step.duration_s for step in timesteps)
    total_minutes = total_seconds / 60.0
    timestep_minutes = [step.duration_s / 60.0 for step in timesteps]

    grid_import_kwh = 0.0
    grid_export_kwh = 0.0
    grid_net_kwh = 0.0
    grid_import_violation_kwh = 0.0
    grid_import_kw_max: float | None = None
    grid_export_kw_max: float | None = None
    grid_net_kw_max: float | None = None
    grid_net_kw_min: float | None = None

    load_base_kwh = 0.0
    load_total_kwh = 0.0
    ev_charge_kwh = 0.0
    pv_kwh = 0.0
    battery_charge_kwh = 0.0
    battery_discharge_kwh = 0.0

    segment_cost_total = 0.0
    price_import_min: float | None = None
    price_import_max: float | None = None
    price_export_min: float | None = None
    price_export_max: float | None = None
    price_import_weighted = 0.0
    price_export_weighted = 0.0

    inverter_stats: dict[str, dict[str, float | None]] = {}
    ev_stats: dict[str, dict[str, float | None]] = {}

    for step in timesteps:
        duration_hours = step.duration_s / 3600.0
        duration_minutes = step.duration_s / 60.0

        grid_import_kwh += step.grid.import_kw * duration_hours
        grid_export_kwh += step.grid.export_kw * duration_hours
        grid_net_kwh += step.grid.net_kw * duration_hours
        if step.grid.import_violation_kw is not None:
            grid_import_violation_kwh += step.grid.import_violation_kw * duration_hours
        grid_import_kw_max = _update_max(grid_import_kw_max, step.grid.import_kw)
        grid_export_kw_max = _update_max(grid_export_kw_max, step.grid.export_kw)
        grid_net_kw_max = _update_max(grid_net_kw_max, step.grid.net_kw)
        grid_net_kw_min = _update_min(grid_net_kw_min, step.grid.net_kw)

        load_base_kwh += step.loads.base_kw * duration_hours
        load_total_kwh += step.loads.total_kw * duration_hours

        segment_cost_total += step.economics.segment_cost
        price_import_min, price_import_max = _update_min_max(
            price_import_min,
            price_import_max,
            step.economics.price_import,
        )
        price_export_min, price_export_max = _update_min_max(
            price_export_min,
            price_export_max,
            step.economics.price_export,
        )
        price_import_weighted += step.economics.price_import * duration_hours
        price_export_weighted += step.economics.price_export * duration_hours

        step_pv_kw = 0.0
        step_battery_charge_kw = 0.0
        step_battery_discharge_kw = 0.0

        for inverter_id, inverter in step.inverters.items():
            stats = inverter_stats.setdefault(
                inverter_id,
                {
                    "pv_kwh": 0.0,
                    "ac_net_kwh": 0.0,
                    "battery_charge_kwh": 0.0,
                    "battery_discharge_kwh": 0.0,
                    "soc_pct_min": None,
                    "soc_pct_max": None,
                    "soc_pct_end": None,
                    "soc_kwh_min": None,
                    "soc_kwh_max": None,
                    "soc_kwh_end": None,
                    "curtailment_minutes": 0.0,
                },
            )

            pv_kw = inverter.pv_kw or 0.0
            battery_charge_kw = inverter.battery_charge_kw or 0.0
            battery_discharge_kw = inverter.battery_discharge_kw or 0.0
            stats["pv_kwh"] = (stats["pv_kwh"] or 0.0) + pv_kw * duration_hours
            stats["ac_net_kwh"] = (stats["ac_net_kwh"] or 0.0) + inverter.ac_net_kw * duration_hours
            stats["battery_charge_kwh"] = (
                stats["battery_charge_kwh"] or 0.0
            ) + battery_charge_kw * duration_hours
            stats["battery_discharge_kwh"] = (
                stats["battery_discharge_kwh"] or 0.0
            ) + battery_discharge_kw * duration_hours

            stats["soc_pct_min"], stats["soc_pct_max"] = _update_min_max(
                stats.get("soc_pct_min"),
                stats.get("soc_pct_max"),
                inverter.battery_soc_pct,
            )
            stats["soc_kwh_min"], stats["soc_kwh_max"] = _update_min_max(
                stats.get("soc_kwh_min"),
                stats.get("soc_kwh_max"),
                inverter.battery_soc_kwh,
            )
            if inverter.battery_soc_pct is not None:
                stats["soc_pct_end"] = inverter.battery_soc_pct
            if inverter.battery_soc_kwh is not None:
                stats["soc_kwh_end"] = inverter.battery_soc_kwh
            if inverter.curtailment:
                stats["curtailment_minutes"] = (
                    stats.get("curtailment_minutes") or 0.0
                ) + duration_minutes

            step_pv_kw += pv_kw
            step_battery_charge_kw += battery_charge_kw
            step_battery_discharge_kw += battery_discharge_kw

        step_ev_charge_kw = 0.0
        for ev_id, ev in step.loads.evs.items():
            stats = ev_stats.setdefault(
                ev_id,
                {
                    "charge_kwh": 0.0,
                    "soc_kwh_min": None,
                    "soc_kwh_max": None,
                    "soc_kwh_end": None,
                    "soc_pct_min": None,
                    "soc_pct_max": None,
                    "soc_pct_end": None,
                    "connected_minutes": 0.0,
                },
            )
            stats["charge_kwh"] = (stats["charge_kwh"] or 0.0) + ev.charge_kw * duration_hours
            stats["soc_kwh_min"], stats["soc_kwh_max"] = _update_min_max(
                stats.get("soc_kwh_min"),
                stats.get("soc_kwh_max"),
                ev.soc_kwh,
            )
            stats["soc_pct_min"], stats["soc_pct_max"] = _update_min_max(
                stats.get("soc_pct_min"),
                stats.get("soc_pct_max"),
                ev.soc_pct,
            )
            stats["soc_kwh_end"] = ev.soc_kwh
            if ev.soc_pct is not None:
                stats["soc_pct_end"] = ev.soc_pct
            if ev.connected:
                stats["connected_minutes"] = (
                    stats.get("connected_minutes") or 0.0
                ) + duration_minutes

            step_ev_charge_kw += ev.charge_kw

        pv_kwh += step_pv_kw * duration_hours
        battery_charge_kwh += step_battery_charge_kw * duration_hours
        battery_discharge_kwh += step_battery_discharge_kw * duration_hours
        ev_charge_kwh += step_ev_charge_kw * duration_hours

    bucket_seconds = bucket_minutes * 60
    horizon_seconds = (horizon_end - horizon_start).total_seconds()
    bucket_count = max(1, int((horizon_seconds + bucket_seconds - 1) // bucket_seconds))
    bucket_spans: list[tuple[Any, Any]] = []
    bucket_payloads: list[dict[str, Any]] = []
    for index in range(bucket_count):
        bucket_start = horizon_start + timedelta(seconds=index * bucket_seconds)
        bucket_end = min(bucket_start + timedelta(seconds=bucket_seconds), horizon_end)
        bucket_spans.append((bucket_start, bucket_end))
        bucket_payloads.append(
            {
                "start": bucket_start.isoformat(),
                "end": bucket_end.isoformat(),
                "grid_import_kwh": 0.0,
                "grid_export_kwh": 0.0,
                "grid_net_kwh": 0.0,
                "load_kwh": 0.0,
                "pv_kwh": 0.0,
                "battery_charge_kwh": 0.0,
                "battery_discharge_kwh": 0.0,
                "ev_charge_kwh": 0.0,
                "curtailment_minutes": 0.0,
            }
        )

    for step in timesteps:
        step_start = step.start
        step_end = step.end
        step_seconds = step.duration_s

        step_pv_kw = sum((inv.pv_kw or 0.0) for inv in step.inverters.values())
        step_battery_charge_kw = sum(
            (inv.battery_charge_kw or 0.0) for inv in step.inverters.values()
        )
        step_battery_discharge_kw = sum(
            (inv.battery_discharge_kw or 0.0) for inv in step.inverters.values()
        )
        step_ev_charge_kw = sum(ev.charge_kw for ev in step.loads.evs.values())
        curtailment_active = any(inv.curtailment for inv in step.inverters.values())

        cursor = step_start
        while cursor < step_end:
            bucket_index = int((cursor - horizon_start).total_seconds() // bucket_seconds)
            bucket_start, bucket_end = bucket_spans[bucket_index]
            overlap_end = min(step_end, bucket_end)
            overlap_seconds = (overlap_end - cursor).total_seconds()
            fraction = overlap_seconds / step_seconds if step_seconds else 0.0
            duration_hours = (step_seconds * fraction) / 3600.0
            duration_minutes = (step_seconds * fraction) / 60.0
            bucket = bucket_payloads[bucket_index]
            bucket["grid_import_kwh"] += step.grid.import_kw * duration_hours
            bucket["grid_export_kwh"] += step.grid.export_kw * duration_hours
            bucket["grid_net_kwh"] += step.grid.net_kw * duration_hours
            bucket["load_kwh"] += step.loads.total_kw * duration_hours
            bucket["pv_kwh"] += step_pv_kw * duration_hours
            bucket["battery_charge_kwh"] += step_battery_charge_kw * duration_hours
            bucket["battery_discharge_kwh"] += step_battery_discharge_kw * duration_hours
            bucket["ev_charge_kwh"] += step_ev_charge_kw * duration_hours
            if curtailment_active:
                bucket["curtailment_minutes"] += duration_minutes
            cursor = bucket_end

    total_cost = timesteps[-1].economics.cumulative_cost
    price_import_avg = price_import_weighted / (total_seconds / 3600.0)
    price_export_avg = price_export_weighted / (total_seconds / 3600.0)

    summary = {
        "meta": {
            "generated_at": plan.generated_at.isoformat(),
            "status": plan.status,
            "objective_value": plan.objective_value,
            "horizon_start": horizon_start.isoformat(),
            "horizon_end": horizon_end.isoformat(),
            "timesteps": len(timesteps),
            "duration_minutes": total_minutes,
            "timestep_minutes": {
                "min": min(timestep_minutes),
                "max": max(timestep_minutes),
                "avg": total_minutes / len(timesteps),
                "unique": sorted(set(timestep_minutes)),
            },
            "bucket_minutes": bucket_minutes,
        },
        "totals": {
            "grid_import_kwh": grid_import_kwh,
            "grid_export_kwh": grid_export_kwh,
            "grid_net_kwh": grid_net_kwh,
            "grid_import_violation_kwh": grid_import_violation_kwh,
            "grid_import_kw_max": grid_import_kw_max,
            "grid_export_kw_max": grid_export_kw_max,
            "grid_net_kw_max": grid_net_kw_max,
            "grid_net_kw_min": grid_net_kw_min,
            "load_base_kwh": load_base_kwh,
            "load_total_kwh": load_total_kwh,
            "ev_charge_kwh": ev_charge_kwh,
            "pv_kwh": pv_kwh,
            "battery_charge_kwh": battery_charge_kwh,
            "battery_discharge_kwh": battery_discharge_kwh,
            "segment_cost_total": segment_cost_total,
            "total_cost": total_cost,
        },
        "prices": {
            "import_min": price_import_min,
            "import_max": price_import_max,
            "import_avg": price_import_avg,
            "export_min": price_export_min,
            "export_max": price_export_max,
            "export_avg": price_export_avg,
        },
        "inverters": inverter_stats,
        "evs": ev_stats,
        "buckets": bucket_payloads,
    }
    return _round_floats(summary)


def serialize_plan(plan: EmsPlanOutput, *, normalize_timings: bool = True) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    if normalize_timings:
        return normalize_plan_payload(payload)
    return payload
