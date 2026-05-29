# 观察雷达历史视图设计

## 目标

在首页“观察雷达”旁展示近几次日报里的观察对象变化，帮助判断某个对象是持续升温、偶发命中，还是长期安静。

## 范围

本轮只做只读历史视图：

- 从已有 `watch_radar` 表读取历史，不新增抓取、不新增 LLM 调用。
- 默认展示最近 7 个有雷达记录的日期。
- 每个观察对象显示最近状态、活跃天数、总命中数、当前动作，以及每日小点趋势。
- 首页加载时随 `/api/stats` 返回历史数据，同时提供 `/api/watch-radar-history` 方便单独调试。

本轮不做详情页、不做通知、不做复杂图表库。

## 数据形态

接口返回：

```json
{
  "watch_radar_history": [
    {
      "target_id": "ai-agent",
      "name": "AI Agent",
      "type": "topic",
      "latest_status": "active",
      "latest_action": "立即看",
      "latest_confidence": 0.9,
      "latest_match_count": 5,
      "active_days": 2,
      "total_matches": 8,
      "history": [
        {"report_date": "2026-05-27", "status": "quiet", "match_count": 0, "confidence": 0.0},
        {"report_date": "2026-05-28", "status": "active", "match_count": 3, "confidence": 0.8},
        {"report_date": "2026-05-29", "status": "active", "match_count": 5, "confidence": 0.9}
      ]
    }
  ]
}
```

排序规则：最近状态为 active 优先，其次活跃天数、总命中数、名称。

## 前端展示

在 `watchPanel` 中当前 6 张观察卡下面新增“近 7 次走势”。每个对象一行：

- 左侧：名称、最近动作。
- 中间：每日状态点，active 用强调色，quiet 用浅色。
- 右侧：活跃天数、总命中数、最高置信度。

空数据时不显示历史区块。

## 测试

- 数据库单元测试覆盖多日期、多观察对象聚合和排序。
- 页面脚本用 `node --check` 校验语法。
- API 验证 `/api/watch-radar-history` 返回数组。
