from __future__ import annotations

import argparse
import gc
import importlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import load_dotenv, load_settings
from app.db import init_db
from app.http import fetch_text


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    message: str
    critical: bool = True


def _resolve_env_path(config_path: Path, env_path: Path) -> Path:
    return env_path if env_path.is_absolute() else config_path.resolve().parent / env_path


def run_local_checks(config_path: Path, env_path: Path) -> list[CheckResult]:
    results: list[CheckResult] = []

    try:
        importlib.import_module("app.pipeline")
        results.append(CheckResult("import", True, "app.pipeline 可导入"))
    except Exception as exc:
        results.append(CheckResult("import", False, f"app.pipeline 导入失败: {exc}"))

    config = config_path.resolve()
    env = _resolve_env_path(config, env_path)
    if not config.exists():
        results.append(CheckResult("config", False, f"配置文件不存在: {config}"))
        return results

    try:
        settings = load_settings(config, None)
        results.append(CheckResult("config", True, f"已读取配置: {config}"))
    except Exception as exc:
        results.append(CheckResult("config", False, f"配置读取失败: {exc}"))
        return results

    if env.exists():
        try:
            load_dotenv(env)
            results.append(CheckResult("env", True, f"已读取环境文件: {env}", critical=False))
        except Exception as exc:
            results.append(CheckResult("env", False, f"环境文件读取失败: {exc}", critical=False))
    else:
        results.append(CheckResult("env", True, ".env 不存在，将使用公共来源或 .env.example", critical=False))

    try:
        directories = [
            settings.app_path("data_dir"),
            settings.app_path("report_dir"),
            settings.app_path("log_dir"),
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        results.append(CheckResult("directories", True, "数据、报告和日志目录可创建"))
    except Exception as exc:
        results.append(CheckResult("directories", False, f"目录检查失败: {exc}"))
        return results

    db_path = settings.app_path("data_dir") / f"doctor-{os.getpid()}-{uuid.uuid4().hex}.sqlite"
    try:
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        results.append(CheckResult("sqlite", True, f"SQLite 可用: {db_path}"))
    except Exception as exc:
        results.append(CheckResult("sqlite", False, f"SQLite 检查失败: {exc}"))
    finally:
        gc.collect()
        for path in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    return results


def run_network_checks(timeout: int = 20) -> list[CheckResult]:
    checks = {
        "hackernews": "https://hacker-news.firebaseio.com/v0/topstories.json",
        "github": "https://api.github.com/rate_limit",
        "arxiv": "https://export.arxiv.org/api/query?search_query=cat:cs.AI&max_results=1",
        "gdelt": "https://api.gdeltproject.org/api/v2/doc/doc?query=technology&mode=ArtList&format=json&maxrecords=1",
    }
    results: list[CheckResult] = []
    for name, url in checks.items():
        try:
            text = fetch_text(url, timeout=timeout)
            results.append(CheckResult(name, True, f"{len(text)} bytes", critical=False))
        except Exception as exc:
            results.append(CheckResult(name, False, str(exc), critical=False))
    return results


def print_results(results: list[CheckResult]) -> None:
    for result in results:
        status = "OK  " if result.ok else "FAIL"
        critical = "critical" if result.critical else "optional"
        print(f"{status} {result.name} [{critical}]: {result.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Local Intel environment and source access.")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    parser.add_argument("--skip-network", action="store_true", help="Skip network source checks")
    args = parser.parse_args()

    results = run_local_checks(Path(args.config), Path(args.env))
    if not args.skip_network:
        results.extend(run_network_checks())
    print_results(results)

    failed_critical = any(not result.ok and result.critical for result in results)
    raise SystemExit(1 if failed_critical else 0)


if __name__ == "__main__":
    main()
