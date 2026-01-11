import datetime as dt
import json
from dataclasses import dataclass
from typing import Protocol

from hass_energy.lib.home_assistant import (
    HomeAssistantClient,
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
)


@dataclass(frozen=True)
class HomeAssistantHistoryPayload:
    history: list[HomeAssistantHistoryStateDict]
    current_state: HomeAssistantStateDict


@dataclass(frozen=True)
class HomeAssistantServiceCallPayload:
    response: object
    current_state: HomeAssistantStateDict


@dataclass(frozen=True)
class ServiceCallRequest:
    domain: str
    service: str
    payload: dict[str, object]


def service_call_key(request: ServiceCallRequest) -> str:
    payload = json.dumps(request.payload, sort_keys=True, default=str)
    return f"{request.domain}:{request.service}:{payload}"


class HassDataProvider(Protocol):
    def fetch(self) -> None: ...

    def fetch_states(self) -> None: ...

    def fetch_history(self) -> None: ...

    def fetch_service_calls(self) -> None: ...

    def get(self, entity_id: str) -> HomeAssistantStateDict: ...

    def get_history(self, entity_id: str) -> list[HomeAssistantHistoryStateDict]: ...

    def get_service_call(self, request: ServiceCallRequest) -> object: ...

    def mark(self, entity_id: str) -> None: ...

    def mark_history(self, entity_id: str, history_days: int) -> None: ...

    def mark_service_call(self, request: ServiceCallRequest) -> None: ...


class HassDataProviderImpl(HassDataProvider):
    def __init__(self, *, hass_client: HomeAssistantClient) -> None:
        self._hass_client = hass_client
        self.marked_entities: set[str] = set()
        self.marked_history_entities: dict[str, int] = {}
        self.marked_service_calls: dict[str, ServiceCallRequest] = {}

        self._data: dict[str, HomeAssistantStateDict] = {}
        self._history_data: dict[str, list[HomeAssistantHistoryStateDict]] = {}
        self._service_call_data: dict[str, object] = {}

    def fetch(self) -> None:
        self.fetch_states()
        self.fetch_history()
        self.fetch_service_calls()

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

    def fetch_service_calls(self) -> None:
        if not self.marked_service_calls:
            return
        service_call_data: dict[str, object] = {}
        for key, request in self.marked_service_calls.items():
            response = self._hass_client.call_service(
                domain=request.domain,
                service=request.service,
                payload=request.payload,
                return_response=True,
            )
            service_call_data[key] = response
        self._service_call_data = service_call_data

    def snapshot(self) -> dict[str, object]:
        return {
            "states": self._data,
            "history": self._history_data,
            "service_calls": self._service_call_data,
        }

    def get(self, entity_id: str) -> HomeAssistantStateDict:
        return self._data[entity_id]

    def get_history(self, entity_id: str) -> list[HomeAssistantHistoryStateDict]:
        return self._history_data[entity_id]

    def get_service_call(self, request: ServiceCallRequest) -> object:
        key = service_call_key(request)
        return self._service_call_data[key]

    def mark(self, entity_id: str) -> None:
        self.marked_entities.add(entity_id)

    def mark_history(self, entity_id: str, history_days: int) -> None:
        if history_days <= 0:
            raise ValueError("history_days must be positive")
        existing = self.marked_history_entities.get(entity_id)
        if existing is None or history_days > existing:
            self.marked_history_entities[entity_id] = history_days

    def mark_service_call(self, request: ServiceCallRequest) -> None:
        key = service_call_key(request)
        existing = self.marked_service_calls.get(key)
        if existing is None:
            self.marked_service_calls[key] = request
            return
        if existing != request:
            raise ValueError(f"Service call mismatch for {key}")
