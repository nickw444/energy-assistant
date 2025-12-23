
import json
from typing import TypedDict

from hass_energy.lib.home_assistant import HomeAssistantClient


class HomeAssistantStateDict(TypedDict):
    entity_id: str
    state: str|float|int|None
    attributes: dict[str, object]
    last_changed: str
    last_reported: str
    last_updated: str


class HassDataProvider:
    def __init__(self, *, hass_client: HomeAssistantClient) -> None:
        self._hass_client = hass_client
        self.marked_entities: set[str] = set()

        self._data: dict[str, HomeAssistantStateDict] = {}

    def fetch(self) -> None:
        # TODO(NW): Fetch subset of data for marked entities only
        resp = self._hass_client.fetch_realtime_state()
        self._data = { item['entity_id']: item for item in resp }

    def get(self, entity_id: str) -> HomeAssistantStateDict:
        # Simulate fetching data from Home Assistant
        return self._data[entity_id]
    
    def mark(self, entity_id: str) -> None:
        self.marked_entities.add(entity_id)
