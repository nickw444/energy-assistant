from typing import TypeVar, cast

from pydantic import BaseModel

from hass_energy.lib.source_resolver.hass_provider import HassDataProvider
from hass_energy.lib.source_resolver.hass_source import (
    HomeAssistantEntitySource,
    HomeAssistantMultiEntitySource,
)
from hass_energy.lib.source_resolver.sources import EntitySource

Q = TypeVar("Q")
R = TypeVar("R")

class ValueResolver:
    def __init__(self, hass_data_provider: HassDataProvider) -> None:
        self._hass_data_provider = hass_data_provider

    def mark_for_hydration(self, value: object) -> None:
        walk_and_mark_recursively(value, self)

    def hydrate(self) -> None:
        self._hass_data_provider.fetch()

    def resolve(self, source: EntitySource[Q, R]) -> R:
        if isinstance(source, HomeAssistantEntitySource):
            # Simulate fetching data from Home Assistant
            state = self._hass_data_provider.get(source.entity)
            return source.mapper(state)
        if isinstance(source, HomeAssistantMultiEntitySource):
            states = [
                self._hass_data_provider.get(entity)
                for entity in source.entities
            ]
            return source.mapper(states)

        raise ValueError("Unsupported source type")

    def mark(self, source: EntitySource[object, object]) -> None:
        if isinstance(source, HomeAssistantEntitySource):
            self._hass_data_provider.mark(source.entity)
            return
        if isinstance(source, HomeAssistantMultiEntitySource):
            for entity in source.entities:
                self._hass_data_provider.mark(entity)
            return

        raise ValueError("Unsupported source type")

def walk_and_mark_recursively(value: object, resolver: ValueResolver) -> None:
    """Recursively walk all EntitySource fields in a config model and mark them to be fetched."""
    if isinstance(value, EntitySource):
        resolver.mark(cast(EntitySource[object, object], value))
    elif isinstance(value, BaseModel):
        for field_name in value.__class__.model_fields:
            walk_and_mark_recursively(getattr(value, field_name), resolver)
    elif isinstance(value, dict):
        value_dict = cast(dict[object, object], value)
        for item in value_dict.values():
            walk_and_mark_recursively(item, resolver)
    elif isinstance(value, (list, tuple, set)):
        iterable = cast(tuple[object, ...] | list[object] | set[object], value)
        for item in iterable:
            walk_and_mark_recursively(item, resolver)
    # primitives are ignored
    