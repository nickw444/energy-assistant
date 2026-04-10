from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from energy_assistant.ems.models import EmsSeriesPoint
from energy_assistant.ems.planning.horizon import Horizon


def interval_series_points(
    horizon: Horizon,
    values: Sequence[float | bool],
) -> list[EmsSeriesPoint]:
    if len(values) != horizon.num_intervals:
        raise ValueError("interval series length does not match horizon")
    return [
        EmsSeriesPoint(time=slot.start, value=_normalize_value(value))
        for slot, value in zip(horizon.slots, values, strict=True)
    ]


def state_series_points(
    horizon: Horizon,
    values: Sequence[float | bool],
) -> list[EmsSeriesPoint]:
    expected = horizon.num_intervals + 1
    if len(values) != expected:
        raise ValueError("state series length does not match horizon boundaries")
    times: list[datetime] = [horizon.start]
    times.extend(slot.end for slot in horizon.slots)
    return [
        EmsSeriesPoint(time=time, value=_normalize_value(value))
        for time, value in zip(times, values, strict=True)
    ]


def bool_series(values: Iterable[float | bool]) -> list[bool]:
    return [bool(value) for value in values]


def _normalize_value(value: float | bool) -> float | bool:
    if isinstance(value, bool):
        return value
    return float(value)
