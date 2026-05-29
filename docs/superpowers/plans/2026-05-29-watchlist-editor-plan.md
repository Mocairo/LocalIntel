# 观察清单编辑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在配置抽屉里直接编辑 `[[watchlist]]`，保存后驱动“观察雷达”。

**Architecture:** 后端继续以 `interests.toml` 为唯一配置来源，`/api/config` 负责读写观察清单。前端在现有配置抽屉中增加一组动态编辑行，保存时随 interests payload 一起提交。

**Tech Stack:** Python 3.13、标准库 `tomllib`、原生 HTML/CSS/JavaScript、pytest。

---

### Task 1: 配置读写测试

**Files:**
- Modify: `tests/test_config_store.py`
- Modify: `app/config_store.py`

- [x] 写失败测试：`read_ui_config()` 返回 `watchlist`。
- [x] 写失败测试：`update_config()` 保存新的 `watchlist`，过滤空行，并保留未提交时的旧清单。
- [x] 运行 `python -m pytest tests/test_config_store.py -v`，确认新增测试先失败。

### Task 2: 后端实现

**Files:**
- Modify: `app/config_store.py`

- [x] 新增 `normalize_watchlist()` 清洗前端输入。
- [x] 新增 `slugify_watch_id()` 为空 id 生成稳定 id。
- [x] 修改 `read_ui_config()` 返回 `watchlist`。
- [x] 修改 `update_interests()` 在提交 watchlist 时写入新清单，否则保留旧清单。
- [x] 运行 `python -m pytest tests/test_config_store.py -v`，确认通过。

### Task 3: 配置页 UI

**Files:**
- Modify: `app/web.py`

- [x] 配置抽屉新增“观察清单”分区和“新增观察对象”按钮。
- [x] 新增 `renderWatchlistEditor()`、`addWatchTarget()`、`readWatchlistEditor()`。
- [x] `loadConfig()` 渲染 `config.interests.watchlist`。
- [x] `saveConfig()` 提交 `watchlist`。
- [x] 用 `node --check` 校验页面脚本。

### Task 4: 全量验证和提交

**Files:**
- Modify: `README.md`
- Add: `docs/superpowers/specs/2026-05-29-watchlist-editor-design.md`
- Add: `docs/superpowers/plans/2026-05-29-watchlist-editor-plan.md`

- [x] 更新 README 的观察清单说明。
- [x] 运行 `python -m pytest -v`。
- [x] 运行 `python -m compileall app`。
- [x] 验证 `/api/config` 返回 `watchlist`。
- [x] 运行 `git diff --check`。
- [x] 提交 `feat: add watchlist editor`。
