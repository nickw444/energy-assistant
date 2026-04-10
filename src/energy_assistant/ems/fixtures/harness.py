from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from energy_assistant.ems.models import (
    EmsPlanOutput,
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


def resolve_ems_fixture_paths(
    base_dir: Path, fixture: str, scenario: str | None = None
) -> EmsFixturePaths:
    fixture_dir = base_dir / fixture
    scenario_dir = fixture_dir if scenario is None else fixture_dir / scenario
    fixture_config_path = fixture_dir / "config.yaml"
    legacy_fixture_config_path = fixture_dir / "ems_config.yaml"
    scenario_config_path = scenario_dir / "config.yaml"
    if scenario_config_path.exists():
        config_path = scenario_config_path
    elif fixture_config_path.exists():
        config_path = fixture_config_path
    else:
        config_path = legacy_fixture_config_path
    return EmsFixturePaths(
        fixture_dir=fixture_dir,
        scenario_dir=scenario_dir,
        fixture_path=scenario_dir / "input.json",
        fixture_config_path=fixture_config_path,
        scenario_config_path=scenario_config_path,
        config_path=config_path,
        plan_path=scenario_dir / "ems_plan.json",
        plot_path=scenario_dir / "ems_plan.jpeg",
        hash_path=scenario_dir / "ems_plan.hash",
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
def serialize_plan(plan: EmsPlanOutput, *, normalize_timings: bool = True) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    if normalize_timings:
        return normalize_plan_payload(payload)
    return payload
