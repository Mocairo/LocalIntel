from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import init_db
from app.status import build_runtime_status, default_pid_checker, parse_errors, pid_status, read_latest_run


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        root=tmp_path,
        values={
            "app": {
                "timezone": "Asia/Shanghai",
                "daily_time": "08:30",
                "data_dir": "data",
                "report_dir": "reports",
                "log_dir": "logs",
            },
            "web": {"host": "127.0.0.1", "port": 8765},
        },
    )


def test_pid_status_reports_not_tracked_when_pid_file_missing(tmp_path: Path) -> None:
    result = pid_status(tmp_path / "missing.pid", lambda pid: True)

    assert result == {"status": "not_tracked", "pid": 0}


def test_pid_status_reports_running_for_live_pid_file(tmp_path: Path) -> None:
    pid_file = tmp_path / "web.pid"
    pid_file.write_text("1234\n", encoding="utf-8")

    result = pid_status(pid_file, lambda pid: pid == 1234)

    assert result == {"status": "running", "pid": 1234}


def test_default_pid_checker_uses_windows_probe_without_os_kill(monkeypatch) -> None:
    import app.status as status_module

    def fail_kill(pid: int, sig: int) -> None:
        raise AssertionError("os.kill must not be used for Windows PID checks")

    monkeypatch.setattr(status_module.os, "name", "nt", raising=False)
    monkeypatch.setattr(status_module.os, "kill", fail_kill)
    monkeypatch.setattr(status_module, "_windows_pid_exists", lambda pid: pid == 1234, raising=False)

    assert default_pid_checker(1234) is True


def test_windows_pid_exists_declares_api_signatures_and_closes_handle(monkeypatch) -> None:
    import app.status as status_module

    closed_handles: list[int] = []

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    def open_process(access, inherit, pid):
        return 42

    def get_exit_code_process(handle, exit_code_ptr):
        exit_code_ptr._obj.value = 259
        return True

    def close_handle(handle):
        closed_handles.append(handle)
        return True

    fake_kernel32 = SimpleNamespace(
        OpenProcess=FakeFunction(open_process),
        GetExitCodeProcess=FakeFunction(get_exit_code_process),
        CloseHandle=FakeFunction(close_handle),
    )
    monkeypatch.setattr(status_module.ctypes, "windll", SimpleNamespace(kernel32=fake_kernel32), raising=False)

    assert status_module._windows_pid_exists(1234) is True
    assert fake_kernel32.OpenProcess.argtypes == [
        status_module.wintypes.DWORD,
        status_module.wintypes.BOOL,
        status_module.wintypes.DWORD,
    ]
    assert fake_kernel32.OpenProcess.restype == status_module.wintypes.HANDLE
    assert fake_kernel32.GetExitCodeProcess.argtypes == [
        status_module.wintypes.HANDLE,
        status_module.ctypes.POINTER(status_module.wintypes.DWORD),
    ]
    assert fake_kernel32.GetExitCodeProcess.restype == status_module.wintypes.BOOL
    assert fake_kernel32.CloseHandle.argtypes == [status_module.wintypes.HANDLE]
    assert fake_kernel32.CloseHandle.restype == status_module.wintypes.BOOL
    assert closed_handles == [42]


def test_read_latest_run_reports_missing_database(tmp_path: Path) -> None:
    result = read_latest_run(tmp_path / "missing.sqlite")

    assert result["status"] == "not_initialized"


def test_parse_errors_preserves_falsy_non_list_json_values() -> None:
    assert parse_errors("0") == ["0"]
    assert parse_errors("false") == ["False"]


def test_read_latest_run_reads_latest_report_and_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO report_runs
                (report_date, raw_total, deduped_total, inserted, llm_summary, errors_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-28",
                10,
                8,
                8,
                "",
                json.dumps(["rss failed"], ensure_ascii=False),
                "2026-05-28T01:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO source_health
                (report_date, source, status, count, duration_seconds, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-05-28", "rss", "error", 0, 1.2, "timeout", "2026-05-28T01:00:00+00:00"),
        )

    result = read_latest_run(db_path)

    assert result["status"] == "error"
    assert result["report_date"] == "2026-05-28"
    assert result["errors"] == ["rss failed"]
    assert result["source_health"][0]["source"] == "rss"


def test_read_latest_run_reports_degraded_when_source_empty_without_run_error(tmp_path: Path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO report_runs
                (report_date, raw_total, deduped_total, inserted, llm_summary, errors_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-06-01", 0, 0, 0, "", "[]", "2026-06-01T01:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO source_health
                (report_date, source, status, count, duration_seconds, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-06-01", "arxiv", "empty", 0, 1.2, "arXiv 返回 0 条", "2026-06-01T01:00:00+00:00"),
        )

    result = read_latest_run(db_path)

    assert result["status"] == "degraded"
    assert result["source_health"][0]["status"] == "empty"


def test_build_runtime_status_returns_process_and_schedule_summary(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "web.pid").write_text("100\n", encoding="utf-8")
    now = datetime(2026, 5, 28, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    result = build_runtime_status(
        settings,
        now=now,
        pid_checker=lambda pid: pid == 100,
        port_checker=lambda host, port: port == 8765,
    )

    assert result["dashboard"]["status"] == "running"
    assert result["scheduler"]["status"] == "not_tracked"
    assert result["web"]["status"] == "listening"
    assert result["last_run"]["status"] == "not_initialized"
    assert result["next_run_at"] == "2026-05-28T08:30:00+08:00"


def test_build_runtime_status_uses_runtime_web_endpoint_override(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    now = datetime(2026, 5, 28, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    result = build_runtime_status(
        settings,
        now=now,
        pid_checker=lambda pid: False,
        port_checker=lambda host, port: host == "127.0.0.1" and port == 8769,
        web_host="127.0.0.1",
        web_port=8769,
    )

    assert result["web"] == {"status": "listening", "host": "127.0.0.1", "port": 8769}


def test_build_runtime_status_reports_database_error_for_unreadable_sqlite(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "intel.sqlite").write_text("not sqlite", encoding="utf-8")
    now = datetime(2026, 5, 28, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    result = build_runtime_status(
        settings,
        now=now,
        pid_checker=lambda pid: False,
        port_checker=lambda host, port: False,
    )

    assert result["last_run"]["status"] == "error"
    assert result["database"]["status"] == "error"


def test_build_runtime_status_keeps_database_ok_when_latest_run_has_source_error(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "intel.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO report_runs
                (report_date, raw_total, deduped_total, inserted, llm_summary, errors_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-06-01",
                10,
                9,
                9,
                "",
                json.dumps(["gdelt failed"], ensure_ascii=False),
                "2026-06-01T01:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO source_health
                (report_date, source, status, count, duration_seconds, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-06-01", "gdelt", "failed", 0, 1.2, "invalid json", "2026-06-01T01:00:00+00:00"),
        )

    result = build_runtime_status(
        settings,
        now=datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        pid_checker=lambda pid: False,
        port_checker=lambda host, port: False,
    )

    assert result["last_run"]["status"] == "error"
    assert result["database"]["status"] == "ok"
