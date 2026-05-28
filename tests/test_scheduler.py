from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.scheduler import next_run_at


def test_next_run_at_returns_today_when_time_is_still_ahead() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 5, 28, 7, 30, tzinfo=tz)

    result = next_run_at(now, "08:30")

    assert result == datetime(2026, 5, 28, 8, 30, tzinfo=tz)


def test_next_run_at_returns_tomorrow_when_time_has_passed() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 5, 28, 9, 0, tzinfo=tz)

    result = next_run_at(now, "08:30")

    assert result == datetime(2026, 5, 29, 8, 30, tzinfo=tz)
