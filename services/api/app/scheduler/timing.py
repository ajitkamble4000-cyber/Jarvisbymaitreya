"""Pure schedule timing: next occurrences in a job's time zone, converted to UTC.

Nothing here touches the database or the clock. Every function takes ``now`` or ``after`` as an argument,
so the rules are unit-testable.

DST rules:
* A wall-clock time that does not exist (spring gap) runs at the first valid instant after it.
* A wall-clock time that occurs twice (autumn overlap) runs once, at the first occurrence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ScheduleKind = Literal["once", "daily", "weekly"]

TIME_OF_DAY_PATTERN = r"^([01]\d|2[0-3]):([0-5]\d)$"
MAX_TIMEZONE_LENGTH = 64
# Weekly schedules need at most seven days to find an allowed weekday. Ten days leaves room for gap shifts.
_LOOKAHEAD_DAYS = 10
_TIME_RE = re.compile(TIME_OF_DAY_PATTERN)


class ScheduleTimingError(ValueError):
    """A schedule rule is invalid. ``code`` is a stable value the API maps to an error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_zone(name: str) -> ZoneInfo:
    """Resolve an IANA time zone name. Anything else is rejected, including paths and empty strings."""
    if not isinstance(name, str) or not name or len(name) > MAX_TIMEZONE_LENGTH:
        raise ScheduleTimingError("invalid_timezone", "The time zone must be an IANA name such as Europe/London.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ScheduleTimingError("invalid_timezone", "The time zone must be an IANA name such as Europe/London.") from None


def parse_time_of_day(value: str) -> time:
    match = _TIME_RE.match(value) if isinstance(value, str) else None
    if match is None:
        raise ScheduleTimingError("invalid_time", "The time must be HH:MM on a 24-hour clock.")
    return time(int(match.group(1)), int(match.group(2)))


def normalise_weekdays(values: Iterable[int]) -> list[int]:
    """Sorted, unique weekdays with 0 = Monday .. 6 = Sunday. At least one is required."""
    days = sorted(set(values))
    if not days:
        raise ScheduleTimingError("invalid_weekdays", "A weekly schedule needs at least one weekday.")
    if any(day < 0 or day > 6 for day in days):
        raise ScheduleTimingError("invalid_weekdays", "Weekdays are numbered 0 (Monday) to 6 (Sunday).")
    return days


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC. Naive datetimes are rejected rather than guessed."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleTimingError("invalid_time", "The run time must include a UTC offset.")
    return value.astimezone(UTC)


def ensure_future(run_at: datetime, now: datetime) -> datetime:
    """Return the UTC instant, or raise ``schedule_in_past`` when it is not after ``now``."""
    instant = to_utc(run_at)
    if instant <= now.astimezone(UTC):
        raise ScheduleTimingError("schedule_in_past", "That time has already passed. Choose a later time.")
    return instant


def _wall_clock(instant_seconds: int, zone: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(instant_seconds, zone).replace(tzinfo=None)


def _gap_end(naive: datetime, zone: ZoneInfo) -> datetime:
    """First UTC instant at or after a wall-clock time that falls inside a spring gap.

    The transition instant T satisfies naive - after < T <= naive - before, where the offsets are the ones
    either side of the gap. Wall-clock time is monotonic across the gap, so a binary search on whole seconds
    finds T exactly.
    """
    before = naive.replace(tzinfo=zone, fold=0).utcoffset()
    after = naive.replace(tzinfo=zone, fold=1).utcoffset()
    if before is None or after is None:
        raise ScheduleTimingError("invalid_time", "The time cannot be placed in this time zone.")
    low = int((naive.replace(tzinfo=UTC) - after).timestamp())
    high = int((naive.replace(tzinfo=UTC) - before).timestamp())
    while high - low > 1:
        middle = (low + high) // 2
        if _wall_clock(middle, zone) >= naive:
            high = middle
        else:
            low = middle
    return datetime.fromtimestamp(high, UTC)


def instant_for_local(day: date, moment: time, zone: ZoneInfo) -> datetime:
    """The UTC instant of a wall-clock time on a given local date, applying the DST rules above."""
    naive = datetime.combine(day, moment)
    first = naive.replace(tzinfo=zone, fold=0)
    utc_candidate = first.astimezone(UTC)
    if utc_candidate.astimezone(zone).replace(tzinfo=None) == naive:
        return utc_candidate  # exists; in an overlap fold=0 is the first occurrence
    return _gap_end(naive, zone)


def next_occurrence(
    *,
    time_of_day: str,
    zone: ZoneInfo,
    weekdays: Sequence[int] | None,
    after: datetime,
) -> datetime:
    """The first UTC instant strictly after ``after`` that matches the local time and weekday set.

    ``weekdays`` of None means every day (daily). Otherwise it is a normalised list (weekly).
    """
    moment = parse_time_of_day(time_of_day)
    current = after.astimezone(UTC)
    local_day = current.astimezone(zone).date()
    for offset in range(_LOOKAHEAD_DAYS):
        day = local_day + timedelta(days=offset)
        if weekdays is not None and day.weekday() not in weekdays:
            continue
        candidate = instant_for_local(day, moment, zone)
        if candidate > current:
            return candidate
    raise ScheduleTimingError("invalid_time", "No occurrence was found in the next ten days.")


def next_run_after(
    *,
    kind: ScheduleKind,
    after: datetime,
    timezone_name: str,
    time_of_day: str | None = None,
    weekdays: Sequence[int] | None = None,
    run_at: datetime | None = None,
) -> datetime | None:
    """Next UTC run after ``after``. ``None`` means no further run (a once schedule that has fired or passed)."""
    if kind == "once":
        if run_at is None:
            raise ScheduleTimingError("invalid_time", "A one-time schedule needs a run time.")
        instant = to_utc(run_at)
        return instant if instant > after.astimezone(UTC) else None
    if time_of_day is None:
        raise ScheduleTimingError("invalid_time", "A daily or weekly schedule needs a time of day.")
    zone = load_zone(timezone_name)
    if kind == "daily":
        return next_occurrence(time_of_day=time_of_day, zone=zone, weekdays=None, after=after)
    if kind == "weekly":
        days = normalise_weekdays(weekdays or [])
        return next_occurrence(time_of_day=time_of_day, zone=zone, weekdays=days, after=after)
    raise ScheduleTimingError("invalid_time", "Unknown schedule kind.")


__all__ = [
    "MAX_TIMEZONE_LENGTH",
    "TIME_OF_DAY_PATTERN",
    "ScheduleKind",
    "ScheduleTimingError",
    "ensure_future",
    "instant_for_local",
    "load_zone",
    "next_occurrence",
    "next_run_after",
    "normalise_weekdays",
    "parse_time_of_day",
    "to_utc",
]
