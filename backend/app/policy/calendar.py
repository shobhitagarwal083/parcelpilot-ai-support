"""The business calendar. One primitive; every duration in the system reduces to it.

Assumptions A1-A3, none of which are in the source pack and all of which are
stated in the README:

  A1  business week is Mon-Fri, 09:00-18:00 IST
  A2  one business day := 9 business hours := 540 business minutes
  A3  no public holidays are modelled

`coverage` comes from the winning rule and is per-rule, never global. Northstar
marks only P1 as 24x7; Policy v3 marks only Enterprise P1 as 24x7; LumenWorks
disclaims weekend coverage entirely, which needs no special case because the
calendar already excludes weekends.

Getting this wrong is quiet. At the pinned Sunday snapshot a wall-clock
implementation returns the same verdict as a correct one for every ticket -- the
elapsed times are far too short to breach -- while being ~22 hours out on the
due times, promising weekend coverage no agreement sold, and producing false
breaches on Monday morning at the exact moment the clocks actually start.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app import config
from app.policy.types import Coverage


def _midnight(when: datetime) -> datetime:
    return when.replace(hour=0, minute=0, second=0, microsecond=0)


def _day_start(when: datetime) -> datetime:
    return when.replace(hour=config.BUSINESS_START_HOUR, minute=0, second=0, microsecond=0)


def _day_end(when: datetime) -> datetime:
    return when.replace(hour=config.BUSINESS_END_HOUR, minute=0, second=0, microsecond=0)


def is_business_time(when: datetime) -> bool:
    return when.weekday() in config.BUSINESS_DAYS and _day_start(when) <= when < _day_end(when)


def next_business_instant(when: datetime) -> datetime:
    """The first business minute at or after `when`.

    A ticket raised on a Sunday has its clock start on Monday at 09:00; this is
    the function that says so.
    """
    cursor = when
    for _ in range(14):  # a fortnight is far more than enough to find a weekday
        if cursor.weekday() not in config.BUSINESS_DAYS:
            cursor = _midnight(cursor + timedelta(days=1))
            continue
        if cursor < _day_start(cursor):
            return _day_start(cursor)
        if cursor >= _day_end(cursor):
            cursor = _midnight(cursor + timedelta(days=1))
            continue
        return cursor
    raise RuntimeError(f"no business time found within a fortnight of {when}")


def add_business_minutes(start: datetime, minutes: int) -> datetime:
    if minutes < 0:
        raise ValueError("minutes must not be negative")
    cursor = next_business_instant(start)
    remaining = minutes
    while remaining > 0:
        available = int((_day_end(cursor) - cursor).total_seconds() // 60)
        if remaining <= available:
            return cursor + timedelta(minutes=remaining)
        remaining -= available
        cursor = next_business_instant(_day_end(cursor))
    return cursor


def business_minutes_between(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    total = 0
    cursor = start
    while cursor < end:
        if cursor.weekday() in config.BUSINESS_DAYS:
            low = max(cursor, _day_start(cursor))
            high = min(end, _day_end(cursor))
            if high > low:
                total += int((high - low).total_seconds() // 60)
        cursor = _midnight(cursor + timedelta(days=1))
    return total


def add_wall_minutes(start: datetime, minutes: int) -> datetime:
    return start + timedelta(minutes=minutes)


def wall_minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


# ---------------------------------------------------------------- coverage-aware


def add_minutes(start: datetime, minutes: int, *, coverage: Coverage) -> datetime:
    """Advance by `minutes`, honouring the rule's own coverage.

    `24x7` bypasses the calendar entirely. This is the single line that decides
    whether TKT-501 and TKT-505 -- a live production outage and a suspected
    credential exposure, both already contractually late -- are reported as
    breached or as not due until Monday.
    """
    if coverage == "24x7":
        return add_wall_minutes(start, minutes)
    return add_business_minutes(start, minutes)


def elapsed_minutes(start: datetime, end: datetime, *, coverage: Coverage) -> int:
    if coverage == "24x7":
        return wall_minutes_between(start, end)
    return business_minutes_between(start, end)


def describe(when: datetime) -> str:
    """Render an instant in IST regardless of how its tzinfo was constructed.

    Timestamps read back from SQLite carry a fixed +05:30 offset rather than the
    named zone, which %Z renders as "UTC+05:30". Everything in this system is
    IST, so normalise before formatting.
    """
    return when.astimezone(config.TZ).strftime("%a %d %b %Y %H:%M IST")


__all__ = [
    "add_business_minutes",
    "add_minutes",
    "add_wall_minutes",
    "business_minutes_between",
    "describe",
    "elapsed_minutes",
    "is_business_time",
    "next_business_instant",
    "wall_minutes_between",
]
