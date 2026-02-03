import pytest

from hass_energy.models.plant import TimeWindow


def test_time_window_months_normalizes_abbreviations() -> None:
    window = TimeWindow(start="08:00", end="10:00", months=["Jan", "feb", "DEC"])

    assert window.months == ["jan", "feb", "dec"]


@pytest.mark.parametrize(
    "months",
    [
        [],
        [1],
        ["1"],
        ["january"],
        ["foo"],
        ["ja"],
        ["sept"],
        ["jan", "foo"],
        "jan",
    ],
)
def test_time_window_months_rejects_invalid_values(months: object) -> None:
    with pytest.raises(ValueError, match="months"):
        TimeWindow(start="08:00", end="10:00", months=months)
