from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from hass_energy.models.config import EmsConfig
from hass_energy.models.plant import PlantConfig, TimeWindow


@dataclass(frozen=True, slots=True)
class HorizonSlot:
    index: int
    start: datetime
    end: datetime

    @property
    def duration_h(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass(frozen=True, slots=True)
class Horizon:
    now: datetime
    start: datetime
    interval_minutes: int
    num_intervals: int
    slots: list[HorizonSlot]
    import_allowed: list[bool]

    @property
    def T(self) -> range:
        return range(self.num_intervals)

    def dt_hours(self, t: int) -> float:
        return self.slots[t].duration_h

    def time_window(self, t: int) -> tuple[datetime, datetime]:
        slot = self.slots[t]
        return slot.start, slot.end


def build_horizon(config: EmsConfig, plant: PlantConfig, *, now: datetime) -> Horizon:
    interval_minutes = config.interval_duration
    num_intervals = config.num_intervals

    start = _floor_to_interval_boundary(now, interval_minutes)
    slots: list[HorizonSlot] = []
    for idx in range(num_intervals):
        slot_start = start + timedelta(minutes=idx * interval_minutes)
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        slots.append(HorizonSlot(index=idx, start=slot_start, end=slot_end))

    forbidden = plant.grid.import_forbidden_periods
    import_allowed = [_is_import_allowed(slot.start, forbidden) for slot in slots]

    return Horizon(
        now=now,
        start=start,
        interval_minutes=interval_minutes,
        num_intervals=num_intervals,
        slots=slots,
        import_allowed=import_allowed,
    )


def _floor_to_interval_boundary(now: datetime, interval_minutes: int) -> datetime:
    minutes = (now.minute // interval_minutes) * interval_minutes
    return now.replace(minute=minutes, second=0, microsecond=0)


def _is_import_allowed(slot_start: datetime, forbidden_windows: list[TimeWindow]) -> bool:
    if not forbidden_windows:
        return True
    minute_of_day = slot_start.hour * 60 + slot_start.minute
    for window in forbidden_windows:
        start = _parse_hhmm(window.start)
        end = _parse_hhmm(window.end)
        if _minute_in_window(minute_of_day, start, end):
            return False
    return True


def _minute_in_window(minute_of_day: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= minute_of_day < end
    return minute_of_day >= start or minute_of_day < end


def _parse_hhmm(value: str) -> int:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)
