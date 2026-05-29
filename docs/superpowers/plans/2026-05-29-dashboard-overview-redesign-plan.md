# 首页概览化重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把首页改成概览优先的仪表盘结构，并把详细内容拆到左侧导航对应的视图里。

**Architecture:** 继续复用单页 HTML 和现有 API，只在前端增加视图状态、导航和按视图显示/隐藏的区块。这样能把主页面变轻，而不会破坏现有数据流。

**Tech Stack:** Python 3.13、原生 HTML/CSS/JavaScript、pytest。

---

### Task 1: 页面结构测试

**Files:**
- Create: `tests/test_dashboard_layout.py`

- [x] 写失败测试：`DASHBOARD_HTML` 包含左侧导航和视图标记。
- [x] 运行 `python -m pytest tests/test_dashboard_layout.py -v`，确认失败。

### Task 2: 视图状态和导航

**Files:**
- Modify: `app/web.py`

- [x] 新增 `state.view` 和视图切换函数。
- [x] 左侧增加 `概览 / 今日情报 / 观察雷达 / 来源状态` 导航。
- [x] 给首页区块添加视图标记，默认只显示概览。
- [x] 运行页面脚本语法检查。

### Task 3: 概览与详情分区

**Files:**
- Modify: `app/web.py`

- [x] 把命令条、条目列表、主线、来源状态拆到对应视图。
- [x] 让概览页只保留总览、运行、摘要和重点提醒。
- [x] 保留现有数据加载逻辑，不改 API。

### Task 4: 文档、验证、提交

**Files:**
- Modify: `README.md`
- Add: `docs/superpowers/specs/2026-05-29-dashboard-overview-redesign-design.md`
- Add: `docs/superpowers/plans/2026-05-29-dashboard-overview-redesign-plan.md`

- [x] 更新 README，说明首页是概览视图。
- [x] 运行 `python -m pytest -v`。
- [x] 运行 `python -m compileall app`。
- [x] 验证页面标记和默认视图。
- [x] 运行 `git diff --check`。
- [x] 提交 `feat: redesign dashboard overview`。
