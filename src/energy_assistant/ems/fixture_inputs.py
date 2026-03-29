from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from energy_assistant.ems.input_provider import (
    EmsInputProvider,
    FixtureResolvedInputProvider,
    ResolverBackedInputProvider,
)
from energy_assistant.ems.input_registry import ResolvedInputRegistry
from energy_assistant.ems.system.factory import EmsSystemFactory
from energy_assistant.lib.source_resolver.fixtures import (
    FixtureHassDataProvider,
    freeze_hass_source_time,
)
from energy_assistant.lib.source_resolver.resolver import ValueResolverImpl
from energy_assistant.models.inputs import InputValueKind

if TYPE_CHECKING:
    from energy_assistant.ems.horizon import Horizon
    from energy_assistant.models.config import AppConfig


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


class FrozenFixtureResolverInputProvider:
    def __init__(
        self,
        *,
        app_config: AppConfig,
        fixture_path: Path,
        captured_at: str | None,
    ) -> None:
        provider, _ = FixtureHassDataProvider.from_path(fixture_path)
        resolver = ValueResolverImpl(hass_data_provider=provider)
        self._base = ResolverBackedInputProvider(app_config=app_config, resolver=resolver)
        self._captured_at = captured_at

    def mark_for_hydration(self) -> None:
        self._base.mark_for_hydration()

    def hydrate_all(self) -> None:
        self._base.hydrate_all()

    def resolve_for_horizon(self, *, horizon: Horizon) -> ResolvedInputRegistry:
        frozen = None if self._captured_at is None else datetime.fromisoformat(self._captured_at)
        with freeze_hass_source_time(frozen):
            return self._base.resolve_for_horizon(horizon=horizon)

    def grid_price_watch_entity_ids(self) -> set[str]:
        return self._base.grid_price_watch_entity_ids()


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
    app_config: AppConfig,
) -> tuple[EmsInputProvider, str | None]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("EMS fixture payload must be a JSON object")

    captured_at_obj = cast(object, data.get("captured_at"))
    captured_at = captured_at_obj if isinstance(captured_at_obj, str) else None

    if "inputs" in data:
        registry, _ = load_resolved_input_registry(path)
        return FixtureResolvedInputProvider(registry=registry), captured_at

    if "states" in data and "history" in data:
        input_provider = FrozenFixtureResolverInputProvider(
            app_config=app_config,
            fixture_path=path,
            captured_at=captured_at,
        )
        input_provider.mark_for_hydration()
        input_provider.hydrate_all()
        return input_provider, captured_at

    raise ValueError("Unsupported EMS fixture payload format")


def resolve_fixture_input_registry(
    *,
    path: Path,
    app_config: AppConfig,
) -> tuple[ResolvedInputRegistry, str | None]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("EMS fixture payload must be a JSON object")
    if "inputs" in data:
        return load_resolved_input_registry(path)

    input_provider, captured_at = load_fixture_input_provider(path=path, app_config=app_config)
    captured_dt = (
        datetime.fromisoformat(captured_at)
        if captured_at
        else datetime.now().astimezone()
    )
    horizon = EmsSystemFactory(app_config).horizon_shape.build(now=captured_dt)
    return input_provider.resolve_for_horizon(horizon=horizon), captured_at
