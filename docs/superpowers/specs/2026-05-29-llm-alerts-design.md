# LLM 监控提醒设计

## 目标

把首页“监控提醒”从纯硬编码规则升级为“规则筛候选 + 大模型判断 + 本地缓存”的高价值信号面板。页面刷新只读取 SQLite，不直接调用大模型。

## 设计

- 规则层继续从日报条目里筛出候选，保证没有 API key 或模型失败时仍有可用提醒。
- 生成日报时调用大模型一次，让模型从候选里选出最多 4 条高价值信号，并输出结构化 JSON。
- 结果写入 `llm_alerts` 表，`/api/alerts` 优先读取缓存；缓存为空时回退到原规则。
- 模型调用失败不影响日报生成，只记录 `llm_jobs`，页面继续显示规则提醒。
- 前端文案把“监听中/未追踪”改成更易懂的“运行中/未启动”。

## 数据结构

`llm_alerts` 按 `report_date + item_hash + kind` 去重，保存：

- `kind`
- `title`
- `detail`
- `action`
- `confidence`
- `item_hash`
- `item_title`
- `source`
- `url`
- `score`

## 验证

- 单元测试覆盖模型 JSON 规范化。
- 单元测试覆盖 `dashboard_alerts()` 优先读取 LLM 缓存。
- 全量 `pytest` 通过。
- 本地预览页面能显示 LLM 判断结果或规则回退结果。
