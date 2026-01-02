from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from hass_energy.lib.source_resolver.hass_provider import (
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
)


class HassFixture(TypedDict):
    captured_at: str
    states: dict[str, HomeAssistantStateDict]
    history: dict[str, list[HomeAssistantHistoryStateDict]]


def load_hass_fixture(path: Path) -> HassFixture:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Fixture payload must be a JSON object.")
    if "states" not in data or "history" not in data:
        raise ValueError("Fixture payload missing required 'states'/'history' keys.")
    return data  # type: ignore[return-value]


@dataclass(slots=True)
class FixtureHassDataProvider:
    states: dict[str, HomeAssistantStateDict]
    history: dict[str, list[HomeAssistantHistoryStateDict]]

    @classmethod
    def from_path(cls, path: Path) -> tuple[FixtureHassDataProvider, str | None]:
        fixture = load_hass_fixture(path)
        return cls(states=fixture["states"], history=fixture["history"]), fixture.get(
            "captured_at"
        )

    def fetch(self) -> None:
        # Fixtures are pre-hydrated; nothing to fetch.
        return

    def fetch_history(self) -> None:
        return

    def fetch_states(self) -> None:
        return

    def get(self, entity_id: str) -> HomeAssistantStateDict:
        return self.states[entity_id]

    def get_history(self, entity_id: str) -> list[HomeAssistantHistoryStateDict]:
        return self.history[entity_id]

    def mark(self, _entity_id: str) -> None:
        # Fixtures are static; no-op to satisfy resolver interface.
        return

    def mark_history(self, _entity_id: str, _history_days: int) -> None:
        # Fixtures are static; no-op to satisfy resolver interface.
        return
