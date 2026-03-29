from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from energy_assistant.inputs.provider import EmsInputProvider, FixtureResolvedInputProvider
from energy_assistant.inputs.registry import ResolvedInputRegistry
from energy_assistant.models.inputs import InputValueKind


class ResolvedInputsFixture(TypedDict):
    captured_at: str
    inputs: dict[str, dict[str, object]]


def _round_fixture_floats(value: object, *, ndigits: int | None) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if ndigits is None:
            return value
        return round(value, ndigits)
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        rounded: dict[str, object] = {}
        for raw_key, raw_item in mapping.items():
            key = str(raw_key)
            rounded[key] = _round_fixture_floats(raw_item, ndigits=ndigits)
        return rounded
    if isinstance(value, list):
        items = cast(list[object], value)
        rounded_list: list[object] = []
        for item in items:
            rounded_list.append(_round_fixture_floats(item, ndigits=ndigits))
        return rounded_list
    return value


def _fixture_rounding_digits(input_payload: dict[str, object]) -> int | None:
    raw_kind = input_payload.get("kind")
    if not isinstance(raw_kind, str):
        return 6
    try:
        kind = InputValueKind(raw_kind)
    except ValueError:
        return 6
    if kind is InputValueKind.POWER:
        return None
    return 6


def _round_fixture_inputs_payload(
    inputs_payload: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    rounded_inputs: dict[str, dict[str, object]] = {}
    for key, input_payload in inputs_payload.items():
        rounded_inputs[key] = cast(
            dict[str, object],
            _round_fixture_floats(
                input_payload,
                ndigits=_fixture_rounding_digits(input_payload),
            ),
        )
    return rounded_inputs


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
        "inputs": _round_fixture_inputs_payload(inputs.to_payload()),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def load_resolved_input_registry(path: Path) -> tuple[ResolvedInputRegistry, str | None]:
    fixture = load_resolved_inputs_fixture(path)
    return (
        ResolvedInputRegistry.from_payload(cast(dict[str, object], fixture["inputs"])),
        fixture.get("captured_at"),
    )


def load_fixture_input_provider(
    *,
    path: Path,
) -> tuple[EmsInputProvider, str | None]:
    registry, captured_at = load_resolved_input_registry(path)
    return FixtureResolvedInputProvider(registry=registry), captured_at
