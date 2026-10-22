"""Pure schedule timing: daily and weekly occurrences, DST gaps and overlaps, validation of zones, times and run times."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.schedules import ScheduleSpec
from app.scheduler.timing import (
    ScheduleTimingError,
    ensure_future,
    load_zone,
    next_run_after,
    normalise_weekdays,
    parse_time_of_day,
)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def daily(time_of_day: str, zone: str, after: datetime) -> datetime | None:
    return next_run_after(kind="daily", time_of_day=time_of_day, timezone_name=zone, after=after)


# ---- daily ------------------------------------------------------------------------------


def test_daily_before_the_time_picks_today() -> None:
    # 2026-10-19 08:00 UTC is 09:00 in London (BST). 18:00 London is 17:00 UTC the same day.
    assert daily("18:00", "Europe/London", utc(2026, 10, 19, 8, 0)) == utc(2026, 10, 19, 17, 0)


def test_daily_after_the_time_picks_tomorrow() -> None:
    # 06:00 London is 05:00 UTC, already passed at 08:00 UTC, so the next run is tomorrow.
    assert daily("06:00", "Europe/London", utc(2026, 10, 19, 8, 0)) == utc(2026, 10, 20, 5, 0)


def test_daily_is_strictly_after_now() -> None:
    assert daily("09:00", "UTC", utc(2026, 10, 19, 9, 0)) == utc(2026, 10, 20, 9, 0)


def test_daily_in_a_time_zone_west_of_utc() -> None:
    # 09:00 New York on 2026-10-19 is EDT (-4), so 13:00 UTC.
    assert daily("09:00", "America/New_York", utc(2026, 10, 19, 12, 0)) == utc(2026, 10, 19, 13, 0)


# ---- weekly -----------------------------------------------------------------------------


def test_weekly_picks_the_next_allowed_weekday() -> None:
    # 2026-10-19 is a Monday (weekday 0). From Tuesday, Monday and Wednesday at 09:00 UTC give Wednesday.
    result = next_run_after(
        kind="weekly", weekdays=[2, 0], time_of_day="09:00", timezone_name="UTC", after=utc(2026, 10, 20, 10, 0)
    )
    assert result == utc(2026, 10, 21, 9, 0)


def test_weekly_on_the_same_day_before_the_time_stays_today() -> None:
    result = next_run_after(
        kind="weekly", weekdays=[0], time_of_day="09:00", timezone_name="UTC", after=utc(2026, 10, 19, 8, 0)
    )
    assert result == utc(2026, 10, 19, 9, 0)


def test_weekly_wraps_across_the_week_end() -> None:
    # Saturday only: from Monday 2026-10-19 the next Saturday is 2026-10-24.
    result = next_run_after(
        kind="weekly", weekdays=[5], time_of_day="10:00", timezone_name="UTC", after=utc(2026, 10, 19, 12, 0)
    )
    assert result == utc(2026, 10, 24, 10, 0)


def test_weekly_requires_at_least_one_weekday() -> None:
    with pytest.raises(ScheduleTimingError) as caught:
        normalise_weekdays([])
    assert caught.value.code == "invalid_weekdays"


def test_weekday_numbers_outside_zero_to_six_are_rejected() -> None:
    with pytest.raises(ScheduleTimingError):
        normalise_weekdays([7])


# ---- DST: spring forward (gap) ----------------------------------------------------------


def test_new_york_spring_gap_runs_at_the_first_valid_instant_after_it() -> None:
    # 2026-03-08: clocks go from 02:00 EST straight to 03:00 EDT. 02:30 does not exist.
    # The first valid instant after it is 03:00 EDT, which is 07:00 UTC.
    assert daily("02:30", "America/New_York", utc(2026, 3, 7, 17, 0)) == utc(2026, 3, 8, 7, 0)


def test_new_york_after_the_gap_day_uses_the_normal_offset_again() -> None:
    assert daily("02:30", "America/New_York", utc(2026, 3, 8, 7, 0)) == utc(2026, 3, 9, 6, 30)


def test_london_spring_gap_runs_at_the_first_valid_instant_after_it() -> None:
    # 2026-03-29: 01:00 GMT jumps to 02:00 BST. 01:30 does not exist, so the run is 02:00 BST = 01:00 UTC.
    assert daily("01:30", "Europe/London", utc(2026, 3, 28, 12, 0)) == utc(2026, 3, 29, 1, 0)


def test_london_time_after_the_gap_is_unaffected() -> None:
    assert daily("02:30", "Europe/London", utc(2026, 3, 28, 12, 0)) == utc(2026, 3, 29, 1, 30)


# ---- DST: autumn fall back (overlap) ----------------------------------------------------


def test_new_york_overlap_runs_once_at_the_first_occurrence() -> None:
    # 2026-11-01: 01:30 happens at 01:30 EDT (05:30 UTC) and again at 01:30 EST (06:30 UTC).
    first = daily("01:30", "America/New_York", utc(2026, 10, 31, 12, 0))
    assert first == utc(2026, 11, 1, 5, 30)
    # Once the first occurrence has fired, the second one is skipped and the next run is the following day.
    assert daily("01:30", "America/New_York", first) == utc(2026, 11, 2, 6, 30)


def test_london_overlap_runs_once_at_the_first_occurrence() -> None:
    # 2026-10-25: 01:30 happens at 01:30 BST (00:30 UTC) and again at 01:30 GMT (01:30 UTC).
    first = daily("01:30", "Europe/London", utc(2026, 10, 24, 12, 0))
    assert first == utc(2026, 10, 25, 0, 30)
    assert daily("01:30", "Europe/London", first) == utc(2026, 10, 26, 1, 30)


# ---- validation -------------------------------------------------------------------------


@pytest.mark.parametrize("zone", ["Mars/Olympus", "../etc/passwd", "", "America", "UTC\n", "x" * 65])
def test_invalid_time_zones_are_rejected(zone: str) -> None:
    with pytest.raises(ScheduleTimingError) as caught:
        load_zone(zone)
    assert caught.value.code == "invalid_timezone"


@pytest.mark.parametrize("value", ["24:00", "9:00", "12:60", "12-00", "", "noon"])
def test_invalid_times_of_day_are_rejected(value: str) -> None:
    with pytest.raises(ScheduleTimingError) as caught:
        parse_time_of_day(value)
    assert caught.value.code == "invalid_time"


def test_once_in_the_past_is_rejected() -> None:
    with pytest.raises(ScheduleTimingError) as caught:
        ensure_future(utc(2026, 10, 18, 8, 0), utc(2026, 10, 19, 8, 0))
    assert caught.value.code == "schedule_in_past"


def test_once_in_the_future_is_returned_in_utc() -> None:
    assert ensure_future(utc(2026, 10, 20, 8, 0), utc(2026, 10, 19, 8, 0)) == utc(2026, 10, 20, 8, 0)


def test_naive_run_time_is_rejected() -> None:
    with pytest.raises(ScheduleTimingError):
        ensure_future(datetime(2026, 10, 20, 8, 0), utc(2026, 10, 19, 8, 0))


def test_once_next_run_is_the_run_time_until_it_passes() -> None:
    run_at = utc(2026, 10, 20, 8, 0)
    assert next_run_after(kind="once", run_at=run_at, timezone_name="UTC", after=utc(2026, 10, 19, 8, 0)) == run_at
    assert next_run_after(kind="once", run_at=run_at, timezone_name="UTC", after=run_at) is None


# ---- schedule specification (request shape) ---------------------------------------------


def test_spec_accepts_each_kind_with_its_own_fields() -> None:
    assert ScheduleSpec(kind="once", run_at=utc(2026, 10, 20, 8, 0))
    assert ScheduleSpec(kind="daily", time_of_day="07:30", timezone="Europe/London")
    assert ScheduleSpec(kind="weekly", time_of_day="07:30", weekdays=[0, 4])


def test_spec_rejects_fields_that_do_not_belong_to_the_kind() -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="once", run_at=utc(2026, 10, 20, 8, 0), time_of_day="07:30")
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="daily", time_of_day="07:30", weekdays=[0])
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="daily", time_of_day="07:30", run_at=utc(2026, 10, 20, 8, 0))


def test_spec_requires_a_weekday_for_weekly_and_a_time_for_recurring() -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="weekly", time_of_day="07:30", weekdays=[])
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="weekly", time_of_day="07:30")
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="daily")


def test_spec_rejects_a_naive_run_time_and_an_unknown_zone() -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="once", run_at=datetime(2026, 10, 20, 8, 0))
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="daily", time_of_day="07:30", timezone="Mars/Olympus")
