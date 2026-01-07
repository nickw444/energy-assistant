from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


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

    @property
    def T(self) -> range:
        return range(self.num_intervals)

    def dt_hours(self, t: int) -> float:
        return self.slots[t].duration_h

    def time_window(self, t: int) -> tuple[datetime, datetime]:
        slot = self.slots[t]
        return slot.start, slot.end


def build_horizon(
    *,
    now: datetime,
    interval_minutes: int,
    num_intervals: int,
) -> Horizon:
    start = floor_to_interval_boundary(now, interval_minutes)
    slots: list[HorizonSlot] = []
    for idx in range(num_intervals):
        slot_start = start + timedelta(minutes=idx * interval_minutes)
        slot_end = slot_start + timedelta(minutes=interval_minutes)
        slots.append(HorizonSlot(index=idx, start=slot_start, end=slot_end))

    return Horizon(
        now=now,
        start=start,
        interval_minutes=interval_minutes,
        num_intervals=num_intervals,
        slots=slots,
    )


def floor_to_interval_boundary(now: datetime, interval_minutes: int) -> datetime:
    minutes = (now.minute // interval_minutes) * interval_minutes
    return now.replace(minute=minutes, second=0, microsecond=0)
