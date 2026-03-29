from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from energy_assistant.ems.input_registry import ResolvedInputRegistry


class ResolvedInputsFixture(TypedDict):
    captured_at: str
    inputs: dict[str, dict[str, object]]


def load_resolved_inputs_fixture(path: Path) -> ResolvedInputsFixture:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Resolved EMS fixture payload must be a JSON object")
    if "captured_at" not in data or "inputs" not in data:
        raise ValueError("Resolved EMS fixture payload missing required keys")
    raw_inputs = cast(object, data.get("inputs"))
    if not isinstance(raw_inputs, dict):
        raise ValueError("Resolved EMS fixture payload inputs must be an object")
    return {
        "captured_at": cast(str, data["captured_at"]),
        "inputs": cast(dict[str, dict[str, object]], raw_inputs),
    }


def save_resolved_inputs_fixture(
    *,
    path: Path,
    captured_at: str,
    inputs: ResolvedInputRegistry,
) -> None:
    payload = {
        "captured_at": captured_at,
        "inputs": inputs.to_payload(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def load_resolved_input_registry(path: Path) -> tuple[ResolvedInputRegistry, str | None]:
    fixture = load_resolved_inputs_fixture(path)
    return (
        ResolvedInputRegistry.from_payload(cast(dict[str, object], fixture["inputs"])),
        fixture.get("captured_at"),
    )
