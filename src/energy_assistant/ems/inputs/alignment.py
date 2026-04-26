from __future__ import annotations

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from energy_assistant.ems.horizon import Horizon
from energy_assistant.lib.source_resolver.models import PowerForecastInterval, PriceForecastInterval


class ForecastInterval(Protocol):
    start: datetime.datetime
    end: datetime.datetime
    value: float


def validate_forecast_coverage(
    *,
    label: str,
    horizon: Horizon,
    intervals: Sequence[ForecastInterval],
    allow_first_slot_missing: bool = False,
) -> None:
    available_minutes = _forecast_coverage_minutes(
        horizon,
        intervals,
        allow_first_slot_missing=allow_first_slot_missing,
    )
    required_minutes = int(sum(slot.duration_m for slot in horizon.slots))
    if available_minutes >= required_minutes:
        return
    raise ValueError(
        f"{label} coverage is insufficient for the configured horizon: "
        f"required={required_minutes} minutes available={available_minutes} minutes"
    )


def _forecast_coverage_minutes(
    horizon: Horizon,
    intervals: Sequence[ForecastInterval],
    *,
    allow_first_slot_missing: bool = False,
) -> int:
    if not intervals:
        return 0

    ordered = sorted(intervals, key=lambda interval: interval.start)
    covered_minutes = 0

    for slot in horizon.slots:
        slot_seconds = (slot.end - slot.start).total_seconds()
        total_overlap = sum(
            max(
                0.0,
                (
                    min(interval.end, slot.end) - max(interval.start, slot.start)
                ).total_seconds(),
            )
            for interval in ordered
            if interval.start < slot.end and interval.end > slot.start
        )
        coverage_gap = slot_seconds - total_overlap
        if coverage_gap > 1.0:
            if allow_first_slot_missing and slot.index == 0:
                covered_minutes += slot.duration_m
                continue
            break
        covered_minutes += slot.duration_m

    return covered_minutes


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
                weighted_sum += interval.value * overlap
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
        if coverage_gap > 1.0:
            if first_slot_override is not None and slot.index == 0:
                series.append(0.0)
                continue
            raise ValueError("forecast series does not cover the full horizon")
        series.append(weighted_sum / total_overlap)
    if first_slot_override is not None:
        series[0] = first_slot_override
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
