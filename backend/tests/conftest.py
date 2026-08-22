from __future__ import annotations

import pytest

from app import config


@pytest.fixture
def now():
    """The pinned snapshot: 2026-08-16 11:00 IST, a Sunday.

    Injected as a fixture and never read from the system clock, so these tests
    return the same answers in 2030 as they do today.
    """
    return config.SNAPSHOT_AT
