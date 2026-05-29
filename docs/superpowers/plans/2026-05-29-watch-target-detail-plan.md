# 观察对象详情抽屉 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 点击观察雷达对象后，用现有详情抽屉展示该对象最近判断记录。

**Architecture:** `app.db` 从 `watch_radar` 表读取单个 target 的最近记录并聚合摘要，`app.web` 提供 `/api/watch-target`，前端在观察卡片和历史行上绑定点击事件并复用详情抽屉。

**Tech Stack:** Python 3.13、SQLite、原生 HTML/CSS/JavaScript、pytest。

---

### Task 1: 后端详情读取

**Files:**
- Modify: `tests/test_watchlist.py`
- Modify: `app/db.py`

- [x] 写失败测试：`load_watch_target_detail()` 返回 target 汇总和 records 日期倒序。
- [x] 实现 `load_watch_target_detail(path, target_id, days=7)`。
- [x] 运行 `python -m pytest tests/test_watchlist.py -v`。

### Task 2: Web API

**Files:**
- Modify: `app/web.py`

- [x] 导入 `load_watch_target_detail`。
- [x] 新增 `/api/watch-target`，缺少或不存在对象时返回 404。
- [x] 验证 `/api/watch-target?target=ai-agent` 返回 target 和 records。

### Task 3: 前端详情抽屉

**Files:**
- Modify: `app/web.py`

- [x] 观察卡片名称改为 `button[data-watch-target]`。
- [x] 历史走势行增加 `data-watch-target`。
- [x] 新增 `openWatchTargetDetail()` 和渲染函数。
- [x] 给 `watchRadar`、`watchHistory` 绑定点击事件。
- [x] 用 `node --check` 校验页面脚本。

### Task 4: 文档、验证、提交

**Files:**
- Modify: `README.md`
- Add: `docs/superpowers/specs/2026-05-29-watch-target-detail-design.md`
- Add: `docs/superpowers/plans/2026-05-29-watch-target-detail-plan.md`

- [x] 更新 README 的观察雷达说明。
- [x] 运行 `python -m pytest -v`。
- [x] 运行 `python -m compileall app`。
- [x] 验证页面标记和 `/api/watch-target`。
- [x] 运行 `git diff --check`。
- [x] 提交 `feat: add watch target detail`。
