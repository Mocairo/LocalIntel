# 观察清单编辑设计

## 目标

让用户可以在首页“配置”抽屉里直接维护观察清单，不再手动编辑 `interests.toml`。保存后，后续情报刷新和“观察雷达”继续使用同一份 `[[watchlist]]` 配置。

## 范围

本轮只做观察清单的配置编辑能力：

- 在 `/api/config` 返回 `watchlist`。
- 在 `/api/config` 保存 `watchlist`。
- 在配置抽屉中支持新增、编辑、删除、启用和停用观察对象。
- 保存时过滤空名称、空关键词的无效观察对象。

本轮不做历史趋势、不做单独详情页、不做通知。

## 数据结构

前端和后端都使用数组：

```json
[
  {
    "id": "ai-agent",
    "name": "AI Agent",
    "type": "topic",
    "enabled": true,
    "keywords": ["agent", "autonomous agent"],
    "description": "跟踪智能体框架、产品和开源项目"
  }
]
```

`id` 为空时由后端根据名称生成稳定 slug。`enabled` 默认 `true`。`type` 保留当前文本值，默认 `topic`。

## 后端设计

`app.config_store` 新增两个小函数：

- `normalize_watchlist(value)`：把前端传入值清洗为可写入 TOML 的列表。
- `slugify_watch_id(name, used_ids)`：为空 id 生成稳定且不重复的 id。

`read_ui_config()` 从 `interests.toml` 读取现有 `[[watchlist]]` 并返回给前端。`update_interests()` 保存兴趣配置时，如果 payload 含 `watchlist` 就写入清洗后的新值；如果不含，则保留旧值，避免旧前端或部分保存丢数据。

## 前端设计

配置抽屉新增“观察清单”分区，每个观察对象是一行紧凑编辑块：

- 启用复选框
- 名称
- 类型
- 关键词文本域，一行或逗号分隔均可
- 描述
- 删除按钮

点击“新增观察对象”插入一行默认空对象。保存时把行数据序列化到 `payload.interests.watchlist`。

## 测试

新增配置存储测试：

- `read_ui_config()` 能返回 `watchlist`。
- `update_config()` 能保存新的 `watchlist`。
- 空名称或空关键词的观察对象会被过滤。
- 未提交 `watchlist` 时保留已有清单。

前端用页面 HTML 标记和内联脚本 `node --check` 做语法验证。
