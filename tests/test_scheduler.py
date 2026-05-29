from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.scheduler import next_run_at, remove_scheduler_pid, write_scheduler_pid


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


def test_scheduler_pid_file_round_trip(tmp_path) -> None:
    settings = Settings(root=tmp_path, values={"app": {"data_dir": "data"}})

    pid_path = write_scheduler_pid(settings, 12345)

    assert pid_path == tmp_path / "data" / "scheduler.pid"
    assert pid_path.read_text(encoding="utf-8") == "12345\n"

    remove_scheduler_pid(pid_path, 54321)
    assert pid_path.exists()

    remove_scheduler_pid(pid_path, 12345)
    assert not pid_path.exists()
