from __future__ import annotations

from datetime import UTC, datetime

from energy_assistant.ems.components.lib.time_windows import TimeWindowMatcher
from energy_assistant.models.plant import TimeWindow


def test_matches_wraparound_window() -> None:
    matcher = TimeWindowMatcher()
    windows = [TimeWindow(start="23:00", end="06:00")]

    assert matcher.matches(windows, datetime(2026, 1, 1, 23, 30, tzinfo=UTC))
    assert matcher.matches(windows, datetime(2026, 1, 2, 5, 59, tzinfo=UTC))
    assert not matcher.matches(windows, datetime(2026, 1, 2, 6, 0, tzinfo=UTC))


def test_matches_respects_month_filters() -> None:
    matcher = TimeWindowMatcher()
    windows = [TimeWindow(start="09:00", end="17:00", months=["jan", "feb"])]

    assert matcher.matches(windows, datetime(2026, 1, 1, 10, 0, tzinfo=UTC))
    assert not matcher.matches(windows, datetime(2026, 3, 1, 10, 0, tzinfo=UTC))


def test_start_equals_end_never_matches() -> None:
    matcher = TimeWindowMatcher()
    windows = [TimeWindow(start="12:00", end="12:00")]

    assert not matcher.matches(windows, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))


def test_allows_empty_windows_but_matches_does_not() -> None:
    matcher = TimeWindowMatcher()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    assert matcher.allows([], now)
    assert not matcher.matches([], now)
