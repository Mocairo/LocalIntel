# 主分支状态

日期：2026-06-01

`reliability-foundation` 工作树已经提升为项目主线。日常运行、测试、配置和继续开发都以 `main` 分支为准。

## 日常入口

- 项目目录：`D:\python_code\local-intel`
- 默认分支：`main`
- 本地网页：`http://127.0.0.1:8765/`

新的使用说明、脚本示例和排查步骤都应该指向 `D:\python_code\local-intel`。不要在新文档里把 `.worktrees\reliability-foundation` 写成用户入口；它只适合作为历史开发工作树或临时对照目录。

## 维护约定

- `main` 是情报中心的当前基线。
- `config.toml`、`.env`、`data/`、`reports/` 和 `logs/` 都是本地状态，不提交到仓库。
- `.env.example` 只能保留占位值，不能写入真实 API key。
- 修改运行能力后，至少执行 `python -m pytest`。

## 已并入主线的能力

- 可靠性基础：测试基线、本地诊断、运行状态和配置卫生。
- 仪表盘：新版概览、今日情报工作台、阅读工作流和观察雷达。
- 观察清单：网页编辑、对象详情、历史趋势和建议动作。
- LLM 与翻译：摘要回退、中文翻译和可配置模型上下文。
