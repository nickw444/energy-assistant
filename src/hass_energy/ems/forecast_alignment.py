from __future__ import annotations

import bisect
import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from hass_energy.ems.horizon import Horizon
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval

# Amber forecasts sometimes offset interval boundaries by 1s; tolerate small gaps per hour.
_ALIGNMENT_GAP_TOLERANCE_SECONDS = 2.0


class ForecastInterval(Protocol):
    start: datetime.datetime
    end: datetime.datetime
    value: float


def forecast_coverage_slots(
    start: datetime.datetime,
    interval_minutes: int,
    intervals: Sequence[ForecastInterval],
    *,
    allow_first_slot_missing: bool = False,
) -> int:
    """Return the number of contiguous horizon slots covered by the forecast.

    Walks forward from the provided horizon start in fixed-size slots and counts
    how many slots overlap at least one forecast interval. When
    ``allow_first_slot_missing`` is true, the initial slot is allowed to be
    uncovered (to support realtime overrides) but later gaps stop coverage.
    """
    if not intervals:
        return 0

    ordered = sorted(intervals, key=lambda interval: interval.start)
    first_start = ordered[0].start
    last_end = ordered[-1].end
    if first_start == last_end:
        return 0
    if (last_end - first_start).total_seconds() <= 0:
        return 0

    starts = [interval.start for interval in ordered]
    slot_start = start
    delta = datetime.timedelta(minutes=interval_minutes)
    count = 0

    while True:
        slot_end = slot_start + delta
        idx = bisect.bisect_right(starts, slot_start) - 1
        covered = False
        for candidate in (idx, idx + 1):
            if 0 <= candidate < len(ordered):
                interval = ordered[candidate]
                if interval.start < slot_end and interval.end > slot_start:
                    covered = True
                    break
        if not covered:
            if allow_first_slot_missing and count == 0:
                count += 1
                slot_start = slot_end
                continue
            break
        count += 1
        slot_start = slot_end

    return count


def _align_intervals[T: ForecastInterval](
    horizon: Horizon,
    intervals: Sequence[T],
    *,
    first_slot_override: float | None = None,
) -> list[float]:
    if not intervals:
        raise ValueError("forecast series does not cover the full horizon")

    ordered = sorted(intervals, key=lambda interval: interval.start)
    series: list[float] = []

    first_start = ordered[0].start
    last_end = ordered[-1].end
    if first_start == last_end:
        raise ValueError("forecast series has zero duration")
    total_seconds = (last_end - first_start).total_seconds()
    if total_seconds <= 0:
        raise ValueError("forecast series has invalid duration")

    horizon_end = horizon.slots[-1].end
    if horizon_end > last_end:
        raise ValueError("forecast series does not cover the full horizon")

    idx = 0
    for slot in horizon.slots:
        slot_start = slot.start
        slot_end = slot.end
        slot_seconds = (slot_end - slot_start).total_seconds()
        while idx < len(ordered) and ordered[idx].end <= slot_start:
            idx += 1
        total_overlap = 0.0
        weighted_sum = 0.0
        scan = idx
        while scan < len(ordered) and ordered[scan].start < slot_end:
            interval = ordered[scan]
            overlap_start = max(slot_start, interval.start)
            overlap_end = min(slot_end, interval.end)
            overlap = (overlap_end - overlap_start).total_seconds()
            if overlap > 0:
                total_overlap += overlap
                weighted_sum += float(interval.value) * overlap
            if interval.end <= slot_end:
                scan += 1
            else:
                break
        if total_overlap <= 0:
            if first_slot_override is not None and slot.index == 0:
                series.append(0.0)
                continue
            raise ValueError("forecast series does not cover the full horizon")
        coverage_gap = slot_seconds - total_overlap
        if coverage_gap > _ALIGNMENT_GAP_TOLERANCE_SECONDS:
            if first_slot_override is not None and slot.index == 0:
                series.append(0.0)
                continue
            raise ValueError("forecast series does not cover the full horizon")
        series.append(weighted_sum / total_overlap)
    if first_slot_override is not None:
        series[0] = float(first_slot_override)
    if len(series) != horizon.num_intervals:
        raise ValueError("forecast series length mismatch")
    return series


@dataclass(slots=True)
class PowerForecastAligner:
    def align(
        self,
        horizon: Horizon,
        intervals: Sequence[PowerForecastInterval],
        *,
        first_slot_override: float | None = None,
    ) -> list[float]:
        """Align power forecast intervals to the horizon.

        Optionally override the first slot with a realtime value. When an
        override is provided, a missing first slot is permitted.
        """
        return _align_intervals(
            horizon,
            intervals,
            first_slot_override=first_slot_override,
        )


@dataclass(slots=True)
class PriceForecastAligner:
    def align(
        self,
        horizon: Horizon,
        intervals: Sequence[PriceForecastInterval],
        *,
        first_slot_override: float | None = None,
    ) -> list[float]:
        """Align price forecast intervals to the horizon.

        Optionally override the first slot with a realtime value. When an
        override is provided, a missing first slot is permitted.
        """
        return _align_intervals(
            horizon,
            intervals,
            first_slot_override=first_slot_override,
        )
