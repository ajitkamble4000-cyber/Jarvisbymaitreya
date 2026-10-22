"""Chat reminders with "HH:MM" resolve in the user's time zone and follow the scheduler's DST rules."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.task_engine import reminder_instant


def test_hh_mm_is_read_in_the_configured_time_zone() -> None:
    now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)

    run_at = reminder_instant("18:30", timezone_name="Asia/Kolkata", now=now)

    # 18:30 IST is 13:00 UTC.
    assert run_at == datetime(2026, 10, 19, 13, 0, tzinfo=UTC)


def test_hh_mm_without_a_configured_zone_is_utc() -> None:
    now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)

    assert reminder_instant("18:30", timezone_name="UTC", now=now) == datetime(2026, 10, 19, 18, 30, tzinfo=UTC)


def test_a_wall_time_in_a_spring_gap_runs_at_the_first_valid_instant() -> None:
    # 2026-03-08 02:00 to 03:00 does not exist in New York. 02:30 runs at 03:00 EDT, which is 07:00 UTC,
    # the same rule the scheduler applies to recurring jobs.
    now = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)

    run_at = reminder_instant("02:30", timezone_name="America/New_York", now=now)

    assert run_at == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
