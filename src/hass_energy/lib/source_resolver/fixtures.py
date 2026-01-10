from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

from hass_energy.lib.source_resolver.hass_provider import (
    HassDataProvider,
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
    ServiceCallRequest,
    service_call_key,
)


class HassFixture(TypedDict):
    captured_at: str
    states: dict[str, HomeAssistantStateDict]
    history: dict[str, list[HomeAssistantHistoryStateDict]]
    service_calls: dict[str, object]


def load_hass_fixture(path: Path) -> HassFixture:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Fixture payload must be a JSON object.")
    if "states" not in data or "history" not in data:
        raise ValueError("Fixture payload missing required 'states'/'history' keys.")
    fixture = cast(dict[str, object], data)
    fixture.setdefault("service_calls", {})
    return fixture  # type: ignore[return-value]


@dataclass(slots=True)
class FixtureHassDataProvider(HassDataProvider):
    states: dict[str, HomeAssistantStateDict]
    history: dict[str, list[HomeAssistantHistoryStateDict]]
    service_calls: dict[str, object]

    @classmethod
    def from_path(cls, path: Path) -> tuple[FixtureHassDataProvider, str | None]:
        fixture = load_hass_fixture(path)
        return (
            cls(
                states=fixture["states"],
                history=fixture["history"],
                service_calls=fixture["service_calls"],
            ),
            fixture.get("captured_at"),
        )

    def fetch(self) -> None:
        # Fixtures are pre-hydrated; nothing to fetch.
        return

    def fetch_history(self) -> None:
        return

    def fetch_states(self) -> None:
        return

    def fetch_service_calls(self) -> None:
        return

    def get(self, entity_id: str) -> HomeAssistantStateDict:
        return self.states[entity_id]

    def get_history(self, entity_id: str) -> list[HomeAssistantHistoryStateDict]:
        return self.history[entity_id]

    def get_service_call(self, request: ServiceCallRequest) -> object:
        return self.service_calls.get(service_call_key(request), {})

    def mark(self, entity_id: str) -> None:
        # Fixtures are static; no-op to satisfy resolver interface.
        _ = entity_id
        return

    def mark_history(self, entity_id: str, history_days: int) -> None:
        # Fixtures are static; no-op to satisfy resolver interface.
        _ = entity_id
        _ = history_days
        return
    def mark_service_call(self, request: ServiceCallRequest) -> None:
        # Fixtures are static; no-op to satisfy resolver interface.
        _ = request
        return


@contextmanager
def freeze_hass_source_time(frozen: datetime | None) -> Iterator[None]:
    if frozen is None:
        yield
        return

    import hass_energy.lib.source_resolver.hass_source as hass_source

    original_datetime = hass_source.datetime.datetime

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return frozen
            if frozen.tzinfo is None:
                return frozen.replace(tzinfo=tz)
            return frozen.astimezone(tz)

    hass_source.datetime.datetime = FrozenDateTime
    try:
        yield
    finally:
        hass_source.datetime.datetime = original_datetime
