from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.config import load_settings
from app.pipeline import run_pipeline


def next_run_at(now: datetime, daily_time: str) -> datetime:
    hour_text, minute_text = daily_time.split(":", 1)
    target = now.replace(hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def write_scheduler_pid(settings: Settings, pid: int = os.getpid()) -> Path:
    data_dir = settings.app_path("data_dir")
    data_dir.mkdir(parents=True, exist_ok=True)
    pid_path = data_dir / "scheduler.pid"
    pid_path.write_text(f"{pid}\n", encoding="utf-8")
    return pid_path


def remove_scheduler_pid(pid_path: Path, pid: int = os.getpid()) -> None:
    if not pid_path.exists():
        return
    raw = pid_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if raw and raw[0].strip() == str(pid):
        pid_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Local Intel once per day.")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    args = parser.parse_args()

    settings = load_settings(args.config, args.env)
    app = settings.section("app")
    timezone_name = str(app.get("timezone", "UTC"))
    daily_time = str(app.get("daily_time", "08:30"))
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    config_path = str(Path(args.config).resolve())
    env_path = str((Path(args.env) if Path(args.env).is_absolute() else Path(args.config).resolve().parent / args.env).resolve())
    print(f"Local Intel scheduler started. Daily run: {daily_time} {timezone_name}")
    pid_path = write_scheduler_pid(settings)
    try:
        while True:
            target = next_run_at(datetime.now(tz), daily_time)
            print(f"Next run at {target.isoformat(timespec='seconds')}")
            while True:
                seconds = (target - datetime.now(tz)).total_seconds()
                if seconds <= 0:
                    break
                time.sleep(min(60, max(1, seconds)))
            try:
                result = run_pipeline(config_path=config_path, env_path=env_path, run_date="today")
                print(f"Report written: {result['markdown_path']}")
            except Exception as exc:
                print(f"Run failed: {exc}")
            time.sleep(2)
    finally:
        remove_scheduler_pid(pid_path)


if __name__ == "__main__":
    main()
