"""Business calendar, tested independently of any domain.

Assumptions A1-A3 are encoded here: Mon-Fri 09:00-18:00 IST, one business day
is nine business hours, no public holidays. None of that is in the source pack,
so all three are stated in the README.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import config
from app.policy import calendar

IST = config.TZ

# 2026-08-14 Fri | 08-15 Sat | 08-16 Sun (the snapshot) | 08-17 Mon | 08-18 Tue


def at(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


def test_k1_a_sunday_clock_starts_on_monday_morning():
    assert calendar.add_business_minutes(at(8, 16, 11), 240) == at(8, 17, 13)


def test_k2_time_left_on_friday_is_used_before_rolling_to_monday():
    # 60 minutes remain on Friday, so the balance lands at Monday 10:00.
    assert calendar.add_business_minutes(at(8, 14, 17), 120) == at(8, 17, 10)


def test_k3_two_business_days_is_1080_minutes():
    assert calendar.add_business_minutes(at(8, 17, 9), 1080) == at(8, 18, 18)


def test_k4_wall_clock_ignores_the_calendar_entirely():
    assert calendar.add_wall_minutes(at(8, 16, 10, 30), 15) == at(8, 16, 10, 45)


def test_k5_weekend_contributes_no_business_minutes():
    assert calendar.business_minutes_between(at(8, 15, 10), at(8, 17, 10)) == 60


def test_k6_a_sunday_interval_is_zero_business_minutes():
    assert calendar.business_minutes_between(at(8, 16, 9, 45), at(8, 16, 11)) == 0


def test_coverage_selects_the_clock():
    """The single line that decides whether two real breaches are reported.

    TKT-501 and TKT-505 are 24x7 by rule. Under a business calendar they would
    both read as not due until Monday -- a live production outage and a
    suspected credential exposure, each already contractually late.
    """
    created = at(8, 16, 10, 30)
    assert calendar.add_minutes(created, 15, coverage="24x7") == at(8, 16, 10, 45)
    assert calendar.add_minutes(created, 15, coverage="business") == at(8, 17, 9, 15)


def test_snapshot_is_a_sunday():
    assert config.SNAPSHOT_AT.weekday() == 6
    assert not calendar.is_business_time(config.SNAPSHOT_AT)


def test_next_business_instant_is_idempotent_inside_business_hours():
    assert calendar.next_business_instant(at(8, 17, 11)) == at(8, 17, 11)


def test_end_of_business_day_rolls_to_the_next_morning():
    assert calendar.next_business_instant(at(8, 17, 18)) == at(8, 18, 9)


def test_negative_durations_are_rejected_rather_than_guessed():
    with pytest.raises(ValueError):
        calendar.add_business_minutes(at(8, 17, 9), -1)
