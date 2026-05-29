# 观察对象详情抽屉设计

## 目标

点击“观察雷达”里的对象后，直接看到这个对象最近几次的判断记录、动作建议、摘要和代表条目，减少在首页卡片之间来回推断的成本。

## 范围

本轮只做只读详情抽屉：

- 复用现有详情抽屉，不新增独立页面。
- 点击观察雷达卡片名称或历史走势行，打开观察对象详情。
- 后端从已有 `watch_radar` 表读取最近记录，不重新抓取、不调用 LLM。
- 代表条目保留原文链接；如果有 `item_hash`，仍可打开已有条目详情。

本轮不做编辑、通知、复杂因果解释，也不补全每个对象的所有匹配条目，因为当前表只保存每日代表条目。

## 接口

新增：

```text
GET /api/watch-target?target=ai-agent&days=7
```

返回：

```json
{
  "target": {
    "target_id": "ai-agent",
    "name": "AI Agent",
    "type": "topic",
    "latest_status": "active",
    "latest_action": "立即看",
    "latest_report_date": "2026-05-29",
    "active_days": 2,
    "total_matches": 8,
    "max_confidence": 0.9
  },
  "records": [
    {
      "report_date": "2026-05-29",
      "status": "active",
      "summary": "今日出现多个 Agent 项目。",
      "action": "立即看",
      "confidence": 0.9,
      "match_count": 5,
      "item_hash": "hash",
      "item_title": "Agent project",
      "source": "github_trending",
      "url": "https://example.com",
      "score": 91.0
    }
  ]
}
```

无记录时返回 404。

## 前端

观察卡片名称改为按钮，点击打开对象详情。历史走势行也可点击打开相同详情。抽屉结构：

- 顶部：对象名称、最近状态、动作、活跃天数、总命中数。
- “最新判断”：最近摘要和代表条目。
- “近期记录”：按日期倒序展示状态、命中数、置信度、动作、摘要和代表条目链接。

## 测试

- 单元测试：`load_watch_target_detail()` 能按对象返回最近记录、汇总字段和日期倒序。
- 页面脚本语法检查。
- 接口验证：`/api/watch-target?target=...` 返回 target 和 records。
