# 可靠性地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Local Intel 增加基础测试、运行状态、诊断命令、配置卫生和仪表盘运行状态展示。

**Architecture:** 新增 `app/status.py` 作为只读运行状态模块，`app.doctor` 负责本地和网络诊断，`app.web` 只接入一个状态 API 和小块页面展示。测试优先覆盖纯逻辑和状态读取，不让默认测试访问真实网络或依赖本地 `data/intel.sqlite`。

**Tech Stack:** Python 3.11+，标准库，SQLite，PowerShell 脚本，pytest。

---

## 文件结构

- 新增 `pyproject.toml`：项目元数据、Python 版本、pytest 配置。
- 修改 `.gitignore`：忽略本地 `config.toml`。
- 从 Git 索引移除 `config.toml`：保留用户本地文件，不再作为共享配置提交。
- 新增 `tests/`：纯逻辑测试和状态测试。
- 新增 `app/status.py`：读取 PID、端口、数据库运行记录和调度时间，生成运行状态字典。
- 修改 `app/doctor.py`：扩展为本地诊断 + 可选网络诊断。
- 修改 `app/web.py`：新增 `/api/runtime-status`，并在仪表盘展示运行状态。
- 修改 `README.md`：补充安装、配置、测试、诊断、`config.toml` 本地化说明。

---

### Task 1: 项目元数据和本地配置卫生

**Files:**
- Create: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `README.md`
- Remove from Git index only: `config.toml`

- [ ] **Step 1: 新增项目元数据**

创建 `pyproject.toml`：

```toml
[project]
name = "local-intel"
version = "0.1.0"
description = "本地运行的个人情报工作台"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 2: 让本地配置不再被提交**

修改 `.gitignore`，加入：

```gitignore
config.toml
```

从 Git 索引移除 `config.toml`，但保留本地文件：

```powershell
git rm --cached config.toml
```

预期：`config.toml` 文件仍存在于工作目录，但 `git status --short` 显示它被从版本控制中移除。

- [ ] **Step 3: 更新 README 的安装和配置说明**

在 `README.md` 的快速启动前补充：

````markdown
## 安装依赖

建议使用 Python 3.11 或更新版本。

```powershell
python -m pip install -e ".[dev]"
```

如果只运行程序、不执行测试，也可以直接使用 Python 标准库运行当前功能。

## 初始化本地配置

公开仓库只保留 `config.example.toml`。首次运行前复制为本地配置：

```powershell
Copy-Item .\config.example.toml .\config.toml
Copy-Item .\.env.example .\.env
notepad .\.env
```

`config.toml` 和 `.env` 都是本地文件，不应提交到仓库。
````

不要在本任务中写 `python -m app.doctor --config ...` 或 `--skip-network` 文档，因为这些参数要到 Task 4 才会实现。本地诊断说明放到 Task 6 收尾时再补。

- [ ] **Step 4: 运行状态检查**

Run:

```powershell
git status --short
```

Expected:

```text
 M .gitignore
 M README.md
D  config.toml
?? pyproject.toml
```

- [ ] **Step 5: 提交**

```powershell
git add .gitignore README.md pyproject.toml
git add -u config.toml
git commit -m "chore: add project baseline and local config hygiene"
```

---

### Task 2: 增加纯逻辑测试基线

**Files:**
- Create: `tests/test_scheduler.py`
- Create: `tests/test_ranker.py`
- Create: `tests/test_clusters.py`
- Create: `tests/test_config_store.py`

- [ ] **Step 1: 写调度器测试**

创建 `tests/test_scheduler.py`：

```python
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
```

- [ ] **Step 2: 写排序器测试**

创建 `tests/test_ranker.py`：

```python
from __future__ import annotations

from datetime import date

from app.models import Item
from app.preferences import Preferences
from app.ranker import rank_items, source_quality


def preferences() -> Preferences:
    return Preferences(
        priority_topics=["AI", "Python"],
        blocked_keywords=["lottery", "彩票"],
        preferred_languages=["zh", "en"],
        blocked_domains=[],
        preferred_domains=["openai.com"],
        weights={
            "freshness": 0.35,
            "source_quality": 0.2,
            "personal_interest": 0.25,
            "popularity": 0.15,
            "source_bonus": 0.05,
        },
    )


def test_rank_items_drops_blocked_keywords() -> None:
    items = [
        Item(
            source="rss:Example",
            source_id="bad",
            title="lottery result",
            url="https://example.com/bad",
            published_at="2026-05-28T00:00:00+00:00",
            summary="blocked content",
            category="technology",
        ),
        Item(
            source="rss:OpenAI News",
            source_id="good",
            title="AI developer tools",
            url="https://openai.com/news/good",
            published_at="2026-05-28T00:00:00+00:00",
            summary="Python and AI update",
            category="ai",
        ),
    ]

    ranked = rank_items(items, preferences(), date(2026, 5, 28))

    assert [item.source_id for item in ranked] == ["good"]


def test_source_quality_prefers_configured_domain() -> None:
    prefs = preferences()
    preferred = Item(
        source="rss:OpenAI News",
        source_id="preferred",
        title="AI update",
        url="https://openai.com/news/item",
    )
    ordinary = Item(
        source="rss:Other",
        source_id="ordinary",
        title="AI update",
        url="https://example.com/news/item",
    )

    assert source_quality(preferred, prefs) > source_quality(ordinary, prefs)
```

- [ ] **Step 3: 写聚类测试**

创建 `tests/test_clusters.py`：

```python
from __future__ import annotations

from app.clusters import build_clusters
from app.models import Item


def test_build_clusters_groups_related_non_github_items() -> None:
    items = [
        Item(
            source="rss:News A",
            source_id="a",
            title="Iran energy supply risk grows",
            url="https://example.com/a",
            summary="Iran energy supply risk affects oil market and shipping routes",
            category="world_news",
            rank_score=90,
        ),
        Item(
            source="rss:News B",
            source_id="b",
            title="Iran oil market faces supply pressure",
            url="https://example.com/b",
            summary="Iran energy supply pressure affects oil market and shipping routes",
            category="world_news",
            rank_score=80,
        ),
    ]

    clusters = build_clusters(items, limit=10)

    assert len(clusters) == 1
    assert clusters[0]["size"] == 2


def test_build_clusters_keeps_github_trending_items_standalone() -> None:
    items = [
        Item(
            source="github_trending",
            source_id="repo-a",
            title="agent framework",
            url="https://github.com/example/a",
            summary="agent framework for coding",
            category="open_source",
            rank_score=90,
        ),
        Item(
            source="github_trending",
            source_id="repo-b",
            title="agent framework tools",
            url="https://github.com/example/b",
            summary="agent framework for coding",
            category="open_source",
            rank_score=80,
        ),
    ]

    clusters = build_clusters(items, limit=10)

    assert len(clusters) == 2
    assert {cluster["size"] for cluster in clusters} == {1}
```

- [ ] **Step 4: 写配置更新测试**

创建 `tests/test_config_store.py`：

```python
from __future__ import annotations

from pathlib import Path

from app.config_store import update_config


def write_base_files(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    interests_path = tmp_path / "interests.toml"
    config_path.write_text(
        """
[app]
timezone = "Asia/Shanghai"
daily_time = "08:30"
days_back = 1
data_dir = "data"
report_dir = "reports"
log_dir = "logs"
interests_file = "interests.toml"

[github]
enabled = true
limit = 10
trending_since = "daily"
trending_languages = [""]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    env_path.write_text("", encoding="utf-8")
    interests_path.write_text(
        """
[interests]
priority_topics = []
blocked_keywords = []
preferred_languages = ["zh", "en"]

[domains]
blocked = []
preferred = []

[weights]
freshness = 0.35
source_quality = 0.2
personal_interest = 0.25
popularity = 0.15
source_bonus = 0.05
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path, env_path


def test_update_config_normalizes_values_and_ignores_unknown_keys(tmp_path: Path) -> None:
    config_path, env_path = write_base_files(tmp_path)

    settings = update_config(
        config_path,
        env_path,
        {
            "app": {"days_back": "0", "unknown": "value"},
            "github": {"limit": "12", "trending_since": "yearly"},
        },
    )

    assert settings.section("app")["days_back"] == 1
    assert "unknown" not in settings.section("app")
    assert settings.section("github")["limit"] == 12
    assert settings.section("github")["trending_since"] == "daily"
```

- [ ] **Step 5: 运行测试，确认当前纯逻辑通过**

Run:

```powershell
python -m pytest tests/test_scheduler.py tests/test_ranker.py tests/test_clusters.py tests/test_config_store.py -v
```

Expected: 所有测试通过。

- [ ] **Step 6: 提交**

```powershell
git add tests pyproject.toml
git commit -m "test: add core logic baseline"
```

---

### Task 3: 新增运行状态模块

**Files:**
- Create: `app/status.py`
- Create: `tests/test_status.py`

- [ ] **Step 1: 写运行状态测试**

创建 `tests/test_status.py`：

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import init_db
from app.status import build_runtime_status, pid_status, read_latest_run


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


def test_read_latest_run_reports_missing_database(tmp_path: Path) -> None:
    result = read_latest_run(tmp_path / "missing.sqlite")

    assert result["status"] == "not_initialized"


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
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest tests/test_status.py -v
```

Expected: FAIL，错误包含 `No module named 'app.status'`。

- [ ] **Step 3: 实现 `app/status.py`**

创建 `app/status.py`：

```python
from __future__ import annotations

import json
import os
import socket
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
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
```

- [ ] **Step 4: 运行状态测试通过**

Run:

```powershell
python -m pytest tests/test_status.py -v
```

Expected: PASS。

- [ ] **Step 5: 运行已有测试确认无回归**

Run:

```powershell
python -m pytest -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add app/status.py tests/test_status.py
git commit -m "feat: add runtime status summary"
```

---

### Task 4: 扩展本地诊断命令

**Files:**
- Modify: `app/doctor.py`
- Create: `tests/test_doctor.py`

- [ ] **Step 1: 写 doctor 本地检查测试**

创建 `tests/test_doctor.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest tests/test_doctor.py -v
```

Expected: FAIL，错误包含 `cannot import name 'CheckResult'` 或 `cannot import name 'run_local_checks'`。

- [ ] **Step 3: 替换 `app/doctor.py`**

将 `app/doctor.py` 替换为：

```python
from __future__ import annotations

import argparse
import importlib
import sqlite3
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


def run_local_checks(config_path: Path, env_path: Path) -> list[CheckResult]:
    results: list[CheckResult] = []

    try:
        importlib.import_module("app.pipeline")
        results.append(CheckResult("import", True, "项目模块可导入"))
    except Exception as exc:
        results.append(CheckResult("import", False, f"项目模块导入失败：{exc}"))

    if not config_path.exists():
        results.append(CheckResult("config", False, f"配置文件不存在：{config_path}"))
        return results

    try:
        settings = load_settings(config_path, env_path)
        results.append(CheckResult("config", True, f"配置文件可读取：{config_path}"))
    except Exception as exc:
        results.append(CheckResult("config", False, f"配置文件读取失败：{exc}"))
        return results

    if env_path.exists():
        try:
            load_dotenv(env_path)
            results.append(CheckResult("env", True, f"环境文件可读取：{env_path}", critical=False))
        except Exception as exc:
            results.append(CheckResult("env", False, f"环境文件读取失败：{exc}", critical=False))
    else:
        results.append(CheckResult("env", True, f"环境文件不存在，将使用公共来源或 .env.example：{env_path}", critical=False))

    try:
        for key in ("data_dir", "report_dir", "log_dir"):
            settings.app_path(key).mkdir(parents=True, exist_ok=True)
        results.append(CheckResult("directories", True, "数据、报告、日志目录可创建"))
    except Exception as exc:
        results.append(CheckResult("directories", False, f"目录创建失败：{exc}"))

    try:
        db_path = settings.app_path("data_dir") / "doctor.sqlite"
        init_db(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute("SELECT 1").fetchone()
        db_path.unlink(missing_ok=True)
        results.append(CheckResult("sqlite", True, "SQLite 可初始化和读写"))
    except Exception as exc:
        results.append(CheckResult("sqlite", False, f"SQLite 检查失败：{exc}"))

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
        label = "OK  " if result.ok else "FAIL"
        critical = "critical" if result.critical else "optional"
        print(f"{label} {result.name} [{critical}]: {result.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Local Intel setup and network access.")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    parser.add_argument("--skip-network", action="store_true", help="Skip remote source connectivity checks")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = config_path.parent / env_path

    results = run_local_checks(config_path, env_path)
    if not args.skip_network:
        results.extend(run_network_checks())
    print_results(results)
    failed_critical = any((not result.ok) and result.critical for result in results)
    raise SystemExit(1 if failed_critical else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行 doctor 测试通过**

Run:

```powershell
python -m pytest tests/test_doctor.py -v
```

Expected: PASS。

- [ ] **Step 5: 运行本地诊断，不访问网络**

Run:

```powershell
python -m app.doctor --config .\config.toml --env .\.env --skip-network
```

Expected: 输出 `OK import`、`OK config`、`OK directories`、`OK sqlite`，退出码为 0。

- [ ] **Step 6: 运行完整测试**

Run:

```powershell
python -m pytest -v
```

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add app/doctor.py tests/test_doctor.py
git commit -m "feat: expand local diagnostics"
```

---

### Task 5: 接入运行状态 API 和仪表盘展示

**Files:**
- Modify: `app/web.py`

- [ ] **Step 1: 在 `app.web` 中导入状态模块**

在 `app/web.py` 顶部导入区加入：

```python
from app.status import build_runtime_status
```

- [ ] **Step 2: 添加 `/api/runtime-status` 路由**

在 `LocalIntelHandler.do_GET` 中，放在 `/api/stats` 路由之前：

```python
        if parsed.path == "/api/runtime-status":
            self.send_json(build_runtime_status(self.state.settings))
            return
```

- [ ] **Step 3: 添加运行状态样式**

在 `DASHBOARD_HTML` 的 `<style>` 内、`.progress-panel` 附近加入：

```css
    .runtime-panel {
      margin: 0 0 18px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow-soft);
    }
    .runtime-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .runtime-head h2 {
      margin: 0;
      font-size: 16px;
    }
    .runtime-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }
    .runtime-card {
      min-width: 0;
      padding: 10px 11px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--panel);
    }
    .runtime-card b {
      display: block;
      font-size: 13px;
      color: var(--muted);
      font-weight: 600;
    }
    .runtime-card span {
      display: block;
      margin-top: 5px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 14px;
      font-weight: 700;
    }
    .runtime-ok { color: var(--teal); }
    .runtime-warn { color: var(--amber); }
    .runtime-error { color: var(--red); }
```

在 `@media (max-width: 1080px)` 中加入：

```css
      .runtime-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
```

在 `@media (max-width: 760px)` 中加入：

```css
      .runtime-grid { grid-template-columns: 1fr; }
```

- [ ] **Step 4: 添加页面结构**

在 `DASHBOARD_HTML` 中，放在 `<section class="metrics" id="stats"></section>` 后面：

```html
        <section class="runtime-panel" id="runtimePanel">
          <div class="runtime-head">
            <h2>运行状态</h2>
            <span class="meta" id="runtimeUpdated">等待状态</span>
          </div>
          <div class="runtime-grid" id="runtimeGrid"></div>
        </section>
```

- [ ] **Step 5: 添加前端状态加载函数**

在 `<script>` 中、`loadStats` 函数后加入：

```javascript
    async function loadRuntimeStatus() {
      const data = await api("/api/runtime-status");
      renderRuntimeStatus(data);
    }

    function renderRuntimeStatus(data) {
      const lastRun = data.last_run || {};
      const sourceRows = lastRun.source_health || [];
      const failedSources = sourceRows.filter((row) => row.status !== "ok");
      const sourceText = sourceRows.length
        ? `${sourceRows.length - failedSources.length}/${sourceRows.length} 正常`
        : "暂无来源记录";
      const cards = [
        ["仪表盘", processLabel(data.dashboard?.status), processClass(data.dashboard?.status)],
        ["调度器", processLabel(data.scheduler?.status), processClass(data.scheduler?.status)],
        ["上次运行", lastRunLabel(lastRun), lastRun.status === "error" ? "runtime-error" : processClass(lastRun.status)],
        ["下次运行", formatDateTime(data.next_run_at), "runtime-ok"],
        ["来源", sourceText, failedSources.length ? "runtime-error" : "runtime-ok"]
      ];
      $("runtimeGrid").innerHTML = cards.map(([label, value, className]) => `
        <div class="runtime-card">
          <b>${esc(label)}</b>
          <span class="${esc(className)}" title="${esc(value)}">${esc(value)}</span>
        </div>
      `).join("");
      $("runtimeUpdated").textContent = `时区：${esc(data.timezone || "")}`;
    }

    function processLabel(status) {
      return {
        running: "运行中",
        stopped: "已停止",
        not_tracked: "未追踪",
        invalid: "PID 无效",
        listening: "监听中",
        unreachable: "未监听",
        ok: "正常",
        error: "异常",
        not_initialized: "未初始化"
      }[status] || "未知";
    }

    function processClass(status) {
      if (["running", "listening", "ok"].includes(status)) return "runtime-ok";
      if (["not_tracked", "not_initialized"].includes(status)) return "runtime-warn";
      return "runtime-error";
    }

    function lastRunLabel(lastRun) {
      if (!lastRun || !lastRun.status || lastRun.status === "not_initialized") return "暂无运行";
      const when = formatDateTime(lastRun.created_at);
      if (lastRun.status === "error") return `${lastRun.report_date || ""} 异常`;
      return when || lastRun.report_date || "已运行";
    }

    function formatDateTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
      return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    }
```

- [ ] **Step 6: 接入刷新流程**

把 `refresh` 函数改为：

```javascript
    async function refresh() {
      await loadDates();
      await Promise.all([loadStats(), loadRuntimeStatus()]);
      await Promise.all([loadClusters(), loadItems(), loadTrends(), loadWeekly(), loadAlerts()]);
    }
```

在 `runBtn` 点击回调里，`await loadStats();` 后加入：

```javascript
      await loadRuntimeStatus();
```

在 `refreshBtn` 点击回调里，`await refresh();` 保持不变。

- [ ] **Step 7: 启动仪表盘并验证 API**

Run:

```powershell
python -m app.web --config .\config.toml --env .\.env --host 127.0.0.1 --port 8765
```

另开 PowerShell：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8765/api/runtime-status | ConvertTo-Json -Depth 6
```

Expected: JSON 包含 `dashboard`、`scheduler`、`web`、`last_run`、`next_run_at`。

- [ ] **Step 8: 运行测试**

Run:

```powershell
python -m pytest -v
```

Expected: PASS。

- [ ] **Step 9: 提交**

```powershell
git add app/web.py
git commit -m "feat: show runtime status in dashboard"
```

---

### Task 6: 文档收尾和整体验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 补充测试说明**

在 README 常用脚本或联网自检前加入：

````markdown
## 测试

运行默认测试：

```powershell
python -m pytest
```

默认测试不会访问真实网络，也不会依赖本地 `data/intel.sqlite`。
````

- [ ] **Step 2: 补充本地诊断说明**

在 README 的联网自检部分补充：

````markdown
## 本地诊断

运行本地配置、目录、数据库和网络诊断：

```powershell
python -m app.doctor --config .\config.toml --env .\.env
```

只检查本地配置和数据库，不访问网络：

```powershell
python -m app.doctor --config .\config.toml --env .\.env --skip-network
```
````

- [ ] **Step 3: 补充运行状态说明**

在 README 的网页仪表盘能力列表中加入：

```markdown
- 运行状态：仪表盘、调度器、上次运行、下次运行和来源健康摘要
```

- [ ] **Step 4: 运行完整测试**

Run:

```powershell
python -m pytest -v
```

Expected: PASS。

- [ ] **Step 5: 运行本地诊断**

Run:

```powershell
python -m app.doctor --config .\config.toml --env .\.env --skip-network
```

Expected: critical 检查全部 OK，退出码为 0。

- [ ] **Step 6: 检查 Git 状态**

Run:

```powershell
git status --short
```

Expected: 只显示 README 修改；`config.toml` 不应再次作为未跟踪文件出现，因为 `.gitignore` 已忽略它。

- [ ] **Step 7: 提交**

```powershell
git add README.md
git commit -m "docs: document reliability workflow"
```

---

## 最终验证

Run:

```powershell
python -m pytest -v
python -m app.doctor --config .\config.toml --env .\.env --skip-network
powershell -ExecutionPolicy Bypass -File .\status_local_intel.ps1
```

Expected:

- `pytest` 全部通过。
- `app.doctor --skip-network` critical 检查全部 OK。
- `status_local_intel.ps1` 仍能显示仪表盘和调度器状态。

如果需要验证页面：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local_intel.ps1
Invoke-RestMethod -Uri http://127.0.0.1:8765/api/runtime-status | ConvertTo-Json -Depth 6
```

Expected:

- API 返回运行状态 JSON。
- 首页出现“运行状态”区域。

---

## 覆盖性自检

- 工程基线：Task 1、Task 2 覆盖。
- 配置卫生：Task 1、Task 6 覆盖。
- 调度器可见性：Task 3、Task 5 覆盖。
- 本地诊断：Task 4、Task 6 覆盖。
- 仪表盘状态展示：Task 5 覆盖。
- README 和新克隆说明：Task 1、Task 6 覆盖。
