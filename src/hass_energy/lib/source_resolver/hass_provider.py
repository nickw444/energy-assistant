import datetime as dt
from dataclasses import dataclass

from hass_energy.lib.home_assistant import (
    HomeAssistantClient,
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
)


@dataclass(frozen=True)
class HomeAssistantHistoryPayload:
    history: list[HomeAssistantHistoryStateDict]
    current_state: HomeAssistantStateDict


class HassDataProvider:
    def __init__(self, *, hass_client: HomeAssistantClient) -> None:
        self._hass_client = hass_client
        self.marked_entities: set[str] = set()
        self.marked_history_entities: dict[str, int] = {}

        self._data: dict[str, HomeAssistantStateDict] = {}
        self._history_data: dict[str, list[HomeAssistantHistoryStateDict]] = {}

    def fetch(self) -> None:
        self.fetch_states()
        self.fetch_history()

    def fetch_states(self) -> None:
        if not self.marked_entities:
            return
        resp = self._hass_client.fetch_realtime_state()
        data: dict[str, HomeAssistantStateDict] = {}
        for item in resp:
            entity_id = item.get("entity_id")
            if entity_id not in self.marked_entities:
                continue
            data[entity_id] = item
        self._data = data

    def fetch_history(self) -> None:
        if not self.marked_history_entities:
            return

        now = dt.datetime.now().astimezone()
        history_data: dict[str, list[HomeAssistantHistoryStateDict]] = {}
        for entity_id, history_days in self.marked_history_entities.items():
            start_time = now - dt.timedelta(days=history_days)
            history = self._hass_client.fetch_entity_history(
                entity_id=entity_id,
                start_time=start_time,
                end_time=now,
                minimal_response=True,
                no_attributes=True,
            )
            history_data[entity_id] = history
        self._history_data = history_data

    def snapshot(self) -> dict[str, object]:
        return {
            "states": self._data,
            "history": self._history_data,
        }

    def get(self, entity_id: str) -> HomeAssistantStateDict:
        return self._data[entity_id]

    def get_history(self, entity_id: str) -> list[HomeAssistantHistoryStateDict]:
        return self._history_data[entity_id]

    def mark(self, entity_id: str) -> None:
        self.marked_entities.add(entity_id)

    def mark_history(self, entity_id: str, history_days: int) -> None:
        if history_days <= 0:
            raise ValueError("history_days must be positive")
        existing = self.marked_history_entities.get(entity_id)
        if existing is None or history_days > existing:
            self.marked_history_entities[entity_id] = history_days
