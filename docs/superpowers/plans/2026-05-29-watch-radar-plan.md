# 观察雷达 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 新增观察清单/情报雷达，让系统在每次日报更新后判断用户关注对象是否出现值得提醒的变化。

**Architecture:** 使用 `interests.toml` 的 `[[watchlist]]` 作为第一版配置入口。`app/watchlist.py` 负责读取配置、规则匹配和可选 LLM 判断；`app/db.py` 负责缓存；`app/pipeline.py` 在生成日报后写入雷达；`app/web.py` 新增接口和首页区块。

**Tech Stack:** Python 标准库、SQLite、现有 OpenAI-compatible LLM 调用封装、原生 HTML/CSS/JS。

---

### Task 1: 观察清单解析

**Files:**
- Create: `app/watchlist.py`
- Test: `tests/test_watchlist.py`

- [x] 写测试：从 `interests.toml` 读取 `[[watchlist]]`，忽略禁用项和缺少关键词的项。
- [x] 实现 `WatchTarget` 和 `load_watchlist(path)`。
- [x] 运行 `python -m pytest tests/test_watchlist.py -v`。

### Task 2: 本地雷达匹配

**Files:**
- Modify: `app/watchlist.py`
- Test: `tests/test_watchlist.py`

- [x] 写测试：关键词命中标题/摘要/标签时生成雷达行。
- [x] 实现 `build_local_watch_radar(targets, items, report_date, limit=6)`。
- [x] 运行 `python -m pytest tests/test_watchlist.py -v`。

### Task 3: SQLite 缓存

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_watchlist.py`

- [x] 写测试：`record_watch_radar()` 写入后 `load_watch_radar()` 能按分数读取。
- [x] 添加 `watch_radar` 表和读写函数。
- [x] 运行 `python -m pytest tests/test_watchlist.py -v`。

### Task 4: 生成流程和页面

**Files:**
- Modify: `app/pipeline.py`
- Modify: `app/web.py`
- Test: existing tests

- [x] 在 pipeline 保存日报后调用 `build_watch_radar()`。
- [x] 新增 `/api/watch-radar`。
- [x] 首页新增“观察雷达”区块。
- [x] 运行 JS 语法检查和全量测试。
