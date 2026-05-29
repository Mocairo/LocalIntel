# 今日情报页重构执行计划

## 成功标准

- 导航只保留“概览、今日情报、观察雷达”。
- 首页不再显示“原始条目/有效条目/事件主线/收藏/LLM 摘要”那一行 KPI。
- 首页包含来源健康、近期趋势、本周沉淀。
- 今日情报页有明确的工作台布局：筛选侧栏、主线区、情报队列、阅读处理区。
- 测试和本地预览通过。

## 步骤

1. 写测试
   - 更新 dashboard 布局测试，锁定新结构和旧结构移除。
   - 验证：目标测试失败。

2. 改首页
   - 删除 `overviewKpis` 和 `stats` 渲染。
   - 删除来源状态导航与独立 `sources-panel`。
   - 新增 `overviewSourceTrend`、`overviewWeeklyBrief`。
   - 压缩总控台和概览间距。

3. 改今日情报页
   - 用 `today-workbench` 包住今日视图。
   - 将原 command、分类、bucket/read tabs、主线、列表重组到工作台。
   - 保留现有 JS 事件绑定和 API 调用。

4. 验证
   - `python -m pytest tests/test_dashboard_layout.py -q`
   - 页面脚本 `node --check`
   - `python -m compileall app`
   - `python -m pytest -q`
   - 启动预览并检查页面。
