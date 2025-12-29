import datetime as dt
from typing import TypedDict

from hass_energy.lib.home_assistant import HomeAssistantClient


class HomeAssistantStateDict(TypedDict):
    entity_id: str
    state: str | float | int | None
    attributes: dict[str, object]
    last_changed: str
    last_reported: str
    last_updated: str


class HomeAssistantHistoryStateDict(TypedDict, total=False):
    entity_id: str
    state: str | float | int | None
    last_changed: str
    last_reported: str
    last_updated: str


class HassDataProvider:
    def __init__(self, *, hass_client: HomeAssistantClient) -> None:
        self._hass_client = hass_client
        self.marked_entities: set[str] = set()
        self.marked_history_entities: dict[str, int] = {}

        self._data: dict[str, HomeAssistantStateDict] = {}
        self._history_data: dict[str, list[HomeAssistantHistoryStateDict]] = {}

    def fetch(self) -> None:
        # TODO(NW): Fetch subset of data for marked entities only
        resp = self._hass_client.fetch_realtime_state()
        self._data = {item["entity_id"]: item for item in resp}
        if not self.marked_history_entities:
            return

        now = dt.datetime.now().astimezone()
        for entity_id, history_days in self.marked_history_entities.items():
            start_time = now - dt.timedelta(days=history_days)
            history = self._hass_client.fetch_entity_history(
                entity_id=entity_id,
                start_time=start_time,
                end_time=now,
                minimal_response=True,
                no_attributes=True,
            )
            self._history_data[entity_id] = [
                item for item in history if isinstance(item, dict)
            ]

    def get(self, entity_id: str) -> HomeAssistantStateDict:
        # Simulate fetching data from Home Assistant
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
