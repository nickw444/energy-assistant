from __future__ import annotations

import math
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

    @property
    def duration_m(self) -> int:
        return int((self.end - self.start).total_seconds() / 60.0)


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


@dataclass(frozen=True, slots=True)
class HorizonFactory:
    timestep_minutes: int
    horizon_minutes: int
    high_res_timestep_minutes: int | None = None
    high_res_horizon_minutes: int | None = None

    def build(self, *, now: datetime) -> Horizon:
        base_interval_minutes = self.high_res_timestep_minutes or self.timestep_minutes
        start = floor_to_interval_boundary(now, base_interval_minutes)

        # Single-resolution horizon: build fixed-size slots from the base timestep.
        if self.high_res_timestep_minutes is None and self.high_res_horizon_minutes is None:
            slots: list[HorizonSlot] = []
            horizon_end = start + timedelta(minutes=self.horizon_minutes)
            interval_count = math.ceil(self.horizon_minutes / self.timestep_minutes)
            for idx in range(interval_count):
                slot_start = start + timedelta(minutes=idx * self.timestep_minutes)
                slot_end = slot_start + timedelta(minutes=self.timestep_minutes)
                if slot_end > horizon_end:
                    slot_end = horizon_end
                slots.append(HorizonSlot(index=idx, start=slot_start, end=slot_end))
            return Horizon(
                now=now,
                start=start,
                interval_minutes=base_interval_minutes,
                num_intervals=len(slots),
                slots=slots,
            )

        # Multi-resolution horizon: high-res window first, then default timestep.
        if (
            self.high_res_timestep_minutes is None
            or self.high_res_horizon_minutes is None
        ):
            raise ValueError(
                "high_res_timestep_minutes and high_res_horizon_minutes are "
                "required for multi-resolution horizons"
            )
        slots: list[HorizonSlot] = []
        horizon_end = start + timedelta(minutes=self.horizon_minutes)
        high_res_end = start + timedelta(minutes=self.high_res_horizon_minutes)
        if self.timestep_minutes != self.high_res_timestep_minutes:
            # Snap the transition to the next coarse boundary so long slots align to the clock.
            high_res_end = ceil_to_interval_boundary(high_res_end, self.timestep_minutes)
        if high_res_end > horizon_end:
            high_res_end = horizon_end

        cursor = start
        slot_idx = 0
        while cursor < high_res_end:
            # Emit high-resolution slots.
            slot_end = cursor + timedelta(minutes=self.high_res_timestep_minutes)
            if slot_end > high_res_end:
                slot_end = high_res_end
            slots.append(HorizonSlot(index=slot_idx, start=cursor, end=slot_end))
            slot_idx += 1
            cursor = slot_end

        while cursor < horizon_end:
            # Emit default-resolution slots for the remainder.
            slot_end = cursor + timedelta(minutes=self.timestep_minutes)
            if slot_end > horizon_end:
                slot_end = horizon_end
            slots.append(HorizonSlot(index=slot_idx, start=cursor, end=slot_end))
            slot_idx += 1
            cursor = slot_end

        return Horizon(
            now=now,
            start=start,
            interval_minutes=base_interval_minutes,
            num_intervals=len(slots),
            slots=slots,
        )


def floor_to_interval_boundary(now: datetime, interval_minutes: int) -> datetime:
    minutes = (now.minute // interval_minutes) * interval_minutes
    return now.replace(minute=minutes, second=0, microsecond=0)


def ceil_to_interval_boundary(now: datetime, interval_minutes: int) -> datetime:
    floored = floor_to_interval_boundary(now, interval_minutes)
    aligned = now.replace(second=0, microsecond=0)
    if floored == aligned:
        return floored
    return floored + timedelta(minutes=interval_minutes)
