from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

import pytest

from app.doctor import CheckResult, main, run_local_checks


def write_config(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    config_path.write_text(
        """
[app]
timezone = "Asia/Shanghai"
daily_time = "08:30"
data_dir = "data"
report_dir = "reports"
log_dir = "logs"
interests_file = "interests.toml"

[web]
host = "127.0.0.1"
port = 8765
""".strip()
        + "\n",
        encoding="utf-8",
    )
    env_path.write_text("", encoding="utf-8")
    return config_path, env_path


def test_run_local_checks_passes_for_valid_local_setup(tmp_path: Path) -> None:
    config_path, env_path = write_config(tmp_path)

    results = run_local_checks(config_path, env_path)

    assert all(isinstance(result, CheckResult) for result in results)
    assert {result.name for result in results} >= {"config", "env", "directories", "sqlite"}
    assert all(result.ok for result in results if result.critical)


def test_run_local_checks_reports_missing_config(tmp_path: Path) -> None:
    results = run_local_checks(tmp_path / "missing.toml", tmp_path / ".env")

    config = next(result for result in results if result.name == "config")

    assert not config.ok
    assert config.critical


def test_run_local_checks_does_not_replace_existing_doctor_sqlite(tmp_path: Path) -> None:
    config_path, env_path = write_config(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    existing_db = data_dir / "doctor.sqlite"
    with sqlite3.connect(existing_db) as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sentinel (value) VALUES ('keep me')")

    run_local_checks(config_path, env_path)

    with sqlite3.connect(existing_db) as conn:
        value = conn.execute("SELECT value FROM sentinel").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert value == "keep me"
    assert tables == {"sentinel"}


def test_run_local_checks_removes_temporary_sqlite_files(tmp_path: Path) -> None:
    config_path, env_path = write_config(tmp_path)

    run_local_checks(config_path, env_path)

    assert list((tmp_path / "data").glob("doctor-*.sqlite*")) == []


def test_run_local_checks_reports_env_decode_error_as_optional(tmp_path: Path) -> None:
    config_path, env_path = write_config(tmp_path)
    env_path.write_bytes(b"\xff")

    results = run_local_checks(config_path, env_path)

    config = next(result for result in results if result.name == "config")
    env = next(result for result in results if result.name == "env")
    assert config.ok
    assert not env.ok
    assert env.critical is False


def test_main_skip_network_does_not_run_network_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path, env_path = write_config(tmp_path)

    def fail_network() -> list[CheckResult]:
        raise AssertionError("network should be skipped")

    monkeypatch.setattr(sys, "argv", ["doctor", "--config", str(config_path), "--env", str(env_path), "--skip-network"])
    monkeypatch.setattr("app.doctor.run_network_checks", fail_network)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0


def test_main_network_failure_does_not_affect_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path, env_path = write_config(tmp_path)

    monkeypatch.setattr(sys, "argv", ["doctor", "--config", str(config_path), "--env", str(env_path)])
    monkeypatch.setattr(
        "app.doctor.run_network_checks",
        lambda: [CheckResult("network", False, "down", critical=False)],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0


def test_main_critical_local_failure_exits_with_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["doctor", "--config", str(tmp_path / "missing.toml"), "--env", str(tmp_path / ".env"), "--skip-network"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
