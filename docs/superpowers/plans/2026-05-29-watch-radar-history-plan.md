# 观察雷达历史视图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在首页观察雷达中展示最近几次日报的关注对象变化趋势。

**Architecture:** 复用 SQLite `watch_radar` 表做只读聚合，`app.db` 暴露历史读取函数，`app.web` 在 `/api/stats` 和独立 API 中返回历史数据，前端用轻量 DOM/CSS 渲染趋势点。

**Tech Stack:** Python 3.13、SQLite、原生 HTML/CSS/JavaScript、pytest。

---

### Task 1: 数据库历史聚合

**Files:**
- Modify: `tests/test_watchlist.py`
- Modify: `app/db.py`

- [x] 写失败测试：多日期 `watch_radar` 数据能聚合为按对象分组的历史趋势。
- [x] 实现 `load_watch_radar_history(path, days=7)`。
- [x] 运行 `python -m pytest tests/test_watchlist.py -v`。

### Task 2: Web API

**Files:**
- Modify: `app/web.py`

- [x] 导入 `load_watch_radar_history`。
- [x] 新增 `/api/watch-radar-history`。
- [x] 在 `/api/stats` 返回 `watch_radar_history`。
- [x] 用本地服务验证接口返回数组。

### Task 3: 首页展示

**Files:**
- Modify: `app/web.py`

- [x] 在 `watchPanel` 中新增历史容器。
- [x] 增加 `.watch-history` CSS。
- [x] 新增 `renderWatchHistory()` 并在 `renderWatchRadar()` 后调用。
- [x] 用 `node --check` 校验页面脚本。

### Task 4: 文档、验证、提交

**Files:**
- Modify: `README.md`
- Add: `docs/superpowers/specs/2026-05-29-watch-radar-history-design.md`
- Add: `docs/superpowers/plans/2026-05-29-watch-radar-history-plan.md`

- [x] 更新 README 说明观察雷达支持历史走势。
- [x] 运行 `python -m pytest -v`。
- [x] 运行 `python -m compileall app`。
- [x] 验证 `/api/watch-radar-history` 和页面标记。
- [x] 运行 `git diff --check`。
- [x] 提交 `feat: add watch radar history`。
