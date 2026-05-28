from __future__ import annotations

import json
import os
import socket
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.scheduler import next_run_at


PidChecker = Callable[[int], bool]
PortChecker = Callable[[str, int], bool]


def default_pid_checker(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def default_port_checker(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def pid_status(path: Path, pid_checker: PidChecker = default_pid_checker) -> dict[str, object]:
    if not path.exists():
        return {"status": "not_tracked", "pid": 0}
    raw = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    first = raw[0].strip() if raw else ""
    try:
        pid = int(first)
    except ValueError:
        return {"status": "invalid", "pid": 0}
    return {"status": "running" if pid_checker(pid) else "stopped", "pid": pid}


def read_latest_run(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        return {"status": "not_initialized", "report_date": "", "created_at": "", "errors": [], "source_health": []}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            run = conn.execute(
                "SELECT * FROM report_runs ORDER BY report_date DESC LIMIT 1",
            ).fetchone()
            if not run:
                return {
                    "status": "not_initialized",
                    "report_date": "",
                    "created_at": "",
                    "errors": [],
                    "source_health": [],
                }
            health_rows = conn.execute(
                """
                SELECT source, status, count, duration_seconds, error
                FROM source_health
                WHERE report_date = ?
                ORDER BY source
                """,
                (run["report_date"],),
            ).fetchall()
    except sqlite3.Error as exc:
        return {"status": "error", "report_date": "", "created_at": "", "errors": [str(exc)], "source_health": []}

    errors = parse_errors(str(run["errors_json"] or "[]"))
    return {
        "status": "error" if errors else "ok",
        "report_date": str(run["report_date"] or ""),
        "created_at": str(run["created_at"] or ""),
        "raw_total": int(run["raw_total"] or 0),
        "deduped_total": int(run["deduped_total"] or 0),
        "inserted": int(run["inserted"] or 0),
        "errors": errors,
        "source_health": [dict(row) for row in health_rows],
    }


def parse_errors(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return [value] if value else []
    if isinstance(parsed, list):
        return [str(row) for row in parsed if str(row)]
    return [str(parsed)] if parsed else []


def build_runtime_status(
    settings: Settings,
    now: datetime | None = None,
    pid_checker: PidChecker = default_pid_checker,
    port_checker: PortChecker = default_port_checker,
) -> dict[str, object]:
    app = settings.section("app")
    web = settings.section("web")
    timezone_name = str(app.get("timezone", "UTC"))
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    current = now.astimezone(tz) if now else datetime.now(tz)
    daily_time = str(app.get("daily_time", "08:30"))
    data_dir = settings.app_path("data_dir")
    db_path = data_dir / "intel.sqlite"
    host = str(web.get("host", "127.0.0.1"))
    port = int(web.get("port", 8765))
    last_run = read_latest_run(db_path)

    return {
        "dashboard": pid_status(data_dir / "web.pid", pid_checker),
        "scheduler": pid_status(data_dir / "scheduler.pid", pid_checker),
        "web": {
            "status": "listening" if port_checker(host, port) else "unreachable",
            "host": host,
            "port": port,
        },
        "database": {"status": "ok" if db_path.exists() else "not_initialized", "path": str(db_path)},
        "last_run": last_run,
        "next_run_at": next_run_at(current, daily_time).isoformat(timespec="seconds"),
        "timezone": timezone_name,
    }
