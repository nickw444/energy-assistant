from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hass_energy.ems.horizon import build_horizon


def test_build_horizon_with_interval_schedule() -> None:
    now = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = build_horizon(
        now=now,
        timestep_minutes=30,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=10,
        total_minutes=70,
    )

    durations = [slot.duration_m for slot in horizon.slots]
    assert durations == [5, 5, 5, 5, 5, 5, 30, 10]
    assert horizon.start == now
    assert horizon.slots[-1].end == now + timedelta(minutes=70)


def test_build_horizon_allows_partial_final_slot() -> None:
    now = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = build_horizon(
        now=now,
        timestep_minutes=30,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=10,
        total_minutes=65,
    )

    durations = [slot.duration_m for slot in horizon.slots]
    assert durations == [5, 5, 5, 5, 5, 5, 30, 5]
    assert horizon.slots[-1].end == now + timedelta(minutes=65)


def test_schedule_alignment_uses_interval_boundaries() -> None:
    now = datetime(2025, 12, 27, 3, 55, tzinfo=UTC)
    horizon = build_horizon(
        now=now,
        timestep_minutes=30,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=20,
        total_minutes=80,
    )

    aligned_slots = [slot for slot in horizon.slots if slot.duration_m == 30]
    assert aligned_slots
    for slot in aligned_slots:
        assert slot.start.minute in (0, 30)


def test_high_res_window_snaps_forward_to_interval_boundary() -> None:
    now = datetime(2025, 12, 27, 3, 55, tzinfo=UTC)
    horizon = build_horizon(
        now=now,
        timestep_minutes=30,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=35,
        total_minutes=120,
    )

    transitions = [
        slot
        for slot in horizon.slots
        if slot.start.minute in (0, 30) and slot.duration_m == 30
    ]
    assert transitions
    assert transitions[0].start.minute == 30 or transitions[0].start.minute == 0
    assert transitions[0].start >= now


def test_high_res_window_can_be_entire_horizon() -> None:
    now = datetime(2025, 12, 27, 0, 0, tzinfo=UTC)
    horizon = build_horizon(
        now=now,
        timestep_minutes=30,
        high_res_timestep_minutes=5,
        high_res_horizon_minutes=60,
        total_minutes=60,
    )

    durations = {slot.duration_m for slot in horizon.slots}
    assert durations == {5}
    assert horizon.slots[-1].end == now + timedelta(minutes=60)


def test_default_horizon_uses_single_resolution() -> None:
    now = datetime(2025, 12, 27, 0, 2, tzinfo=UTC)
    horizon = build_horizon(
        now=now,
        timestep_minutes=15,
        num_intervals=4,
    )

    durations = [slot.duration_m for slot in horizon.slots]
    assert durations == [15, 15, 15, 15]
    assert horizon.start.minute == 0
