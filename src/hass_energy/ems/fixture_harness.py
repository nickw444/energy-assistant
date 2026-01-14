from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hass_energy.ems.models import EmsPlanOutput


@dataclass(frozen=True, slots=True)
class EmsFixturePaths:
    root_dir: Path
    fixture_path: Path
    config_path: Path
    plan_path: Path


def resolve_ems_fixture_paths(base_dir: Path, name: str | None) -> EmsFixturePaths:
    root_dir = base_dir / name if name else base_dir
    return EmsFixturePaths(
        root_dir=root_dir,
        fixture_path=root_dir / "ems_fixture.json",
        config_path=root_dir / "ems_config.yaml",
        plan_path=root_dir / "ems_plan.json",
    )


def serialize_plan(plan: EmsPlanOutput, *, normalize_timings: bool = True) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    if normalize_timings:
        timings = payload.get("timings")
        if isinstance(timings, dict):
            timings_dict = cast(dict[str, object], timings)
            payload["timings"] = {key: 0.0 for key in timings_dict}
    return payload
