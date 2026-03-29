from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime


@contextmanager
def freeze_hass_source_time(frozen: datetime | None) -> Iterator[None]:
    if frozen is None:
        yield
        return

    import energy_assistant.lib.source_resolver.hass_source as hass_source

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
