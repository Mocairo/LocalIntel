from __future__ import annotations

from pathlib import Path

from app.doctor import CheckResult, run_local_checks


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
