from __future__ import annotations

import bisect
import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from hass_energy.ems.horizon import Horizon
from hass_energy.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval


class ForecastInterval(Protocol):
    start: datetime.datetime
    end: datetime.datetime
    value: float


def _align_intervals[T: ForecastInterval](
    horizon: Horizon,
    intervals: Sequence[T],
    *,
    first_slot_override: float | None = None,
) -> list[float]:
    if not intervals:
        raise ValueError("forecast series does not cover the full horizon")

    ordered = sorted(intervals, key=lambda interval: interval.start)
    starts = [interval.start for interval in ordered]
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

    for slot in horizon.slots:
        slot_start = slot.start
        slot_end = slot.end
        idx = bisect.bisect_right(starts, slot_start) - 1
        value: float | None = None
        for candidate in (idx, idx + 1):
            if 0 <= candidate < len(ordered):
                interval = ordered[candidate]
                if interval.start < slot_end and interval.end > slot_start:
                    value = float(interval.value)
                    break
        if value is None:
            if first_slot_override is not None and slot.index == 0:
                series.append(0.0)
                continue
            raise ValueError("forecast series does not cover the full horizon")
        series.append(value)
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
