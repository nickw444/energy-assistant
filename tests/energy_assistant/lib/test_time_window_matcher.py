from datetime import UTC, datetime

from energy_assistant.ems.time_windows import TimeWindowMatcher
from energy_assistant.models.plant import TimeWindow


def test_matcher_allows_when_no_windows() -> None:
    matcher = TimeWindowMatcher()

    assert matcher.matches([], datetime(2025, 1, 1, 8, 0, tzinfo=UTC)) is False
    assert matcher.allows([], datetime(2025, 1, 1, 8, 0, tzinfo=UTC)) is True


def test_matcher_respects_time_window_bounds() -> None:
    matcher = TimeWindowMatcher()
    windows = [TimeWindow(start="08:00", end="10:00")]

    assert matcher.matches(windows, datetime(2025, 1, 1, 8, 0, tzinfo=UTC)) is True
    assert matcher.matches(windows, datetime(2025, 1, 1, 9, 59, tzinfo=UTC)) is True
    assert matcher.matches(windows, datetime(2025, 1, 1, 10, 0, tzinfo=UTC)) is False


def test_matcher_handles_midnight_wrap() -> None:
    matcher = TimeWindowMatcher()
    windows = [TimeWindow(start="22:00", end="02:00")]

    assert matcher.matches(windows, datetime(2025, 1, 1, 23, 0, tzinfo=UTC)) is True
    assert matcher.matches(windows, datetime(2025, 1, 2, 1, 0, tzinfo=UTC)) is True
    assert matcher.matches(windows, datetime(2025, 1, 2, 3, 0, tzinfo=UTC)) is False


def test_matcher_respects_month_scoping() -> None:
    matcher = TimeWindowMatcher()
    windows = [TimeWindow(start="00:00", end="23:59", months=["jan"])]

    assert matcher.matches(windows, datetime(2025, 1, 15, 8, 0, tzinfo=UTC)) is True
    assert matcher.matches(windows, datetime(2025, 3, 15, 8, 0, tzinfo=UTC)) is False
