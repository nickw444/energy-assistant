from __future__ import annotations

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from energy_assistant.models.plant import TimeWindow


def _parse_hhmm(value: str) -> int:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)


def _minute_in_window(minute_of_day: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= minute_of_day < end
    return minute_of_day >= start or minute_of_day < end


def _month_abbr(value: datetime) -> str:
    return calendar.month_abbr[value.month].lower()


@dataclass(frozen=True, slots=True)
class TimeWindowMatcher:
    def matches(self, windows: Iterable[TimeWindow], when: datetime) -> bool:
        window_list = tuple(windows)
        return self._matches(window_list, when)

    def allows(self, windows: Iterable[TimeWindow], when: datetime) -> bool:
        window_list = tuple(windows)
        if not window_list:
            return True
        return self._matches(window_list, when)

    @staticmethod
    def _matches(windows: Sequence[TimeWindow], when: datetime) -> bool:
        if not windows:
            return False
        minute_of_day = when.hour * 60 + when.minute
        month_abbr = _month_abbr(when)
        for window in windows:
            if window.months is not None and month_abbr not in window.months:
                continue
            start = _parse_hhmm(window.start)
            end = _parse_hhmm(window.end)
            if _minute_in_window(minute_of_day, start, end):
                return True
        return False
