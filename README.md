# 知微情报中枢

本地运行的个人情报工作台。它会从 GitHub Trending、arXiv、Hacker News、GDELT、RSS 等来源抓取信息，做本地评分、分类、摘要、翻译、事件主线聚合，并通过本地网页仪表盘查看。

默认网页地址：

```text
http://127.0.0.1:8765/
```

## 安装依赖

建议使用 Python 3.11 或更新版本。

```powershell
python -m pip install -e ".[dev]"
```

如果只运行程序、不执行测试，也可以直接使用 Python 标准库运行当前功能。

## 初始化本地配置

公开仓库只保留 `config.example.toml`。首次运行前复制为本地配置：

```powershell
Copy-Item .\config.example.toml .\config.toml
Copy-Item .\.env.example .\.env
notepad .\.env
```

`config.toml` 和 `.env` 都是本地文件，不应提交到仓库。

## 快速启动

在 PowerShell 中进入项目目录：

```powershell
cd D:\python_code\local-intel
```

启动网页仪表盘和每日调度器：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local_intel.ps1
```

启动后会自动打开网页。如果只想后台启动，不打开浏览器：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local_intel.ps1 -NoBrowser
```

## 停止与状态

查看当前运行状态：

```powershell
powershell -ExecutionPolicy Bypass -File .\status_local_intel.ps1
```

停止网页仪表盘和每日调度器：

```powershell
powershell -ExecutionPolicy Bypass -File .\stop_local_intel.ps1
```

日志文件在：

```text
D:\python_code\local-intel\logs\web.out.log
D:\python_code\local-intel\logs\web.err.log
D:\python_code\local-intel\logs\scheduler.out.log
D:\python_code\local-intel\logs\scheduler.err.log
```

## 常驻运行

如果希望它每天自动抓取，电脑需要保持开机，且不要进入睡眠。浏览器可以关闭，网页不是必须一直打开；只要后台调度器还在运行，就会按配置时间自动执行。

默认每日运行时间在 `config.toml`：

```toml
[app]
daily_time = "08:30"
timezone = "Asia/Shanghai"
```

如果电脑关机或睡眠，程序无法在那个时间抓取。电脑唤醒后可以手动运行一次：

```powershell
python -m app.pipeline --env .\.env
```

## 开机自动启动

注册 Windows 登录后自动启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
```

取消开机自启：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_autostart.ps1
```

开机自启会在你登录 Windows 后后台启动本地网页和每日调度器。

## 手动运行一次抓取

运行今天的数据抓取、评分、摘要和报告生成：

```powershell
python -m app.pipeline --env .\.env
```

指定日期运行：

```powershell
python -m app.pipeline --date 2026-05-28 --env .\.env
```

生成结果会保存在：

```text
D:\python_code\local-intel\data\intel.sqlite
D:\python_code\local-intel\reports\
D:\python_code\local-intel\logs\
```

## 配置 API Token

复制示例环境文件：

```powershell
Copy-Item .\.env.example .\.env
notepad .\.env
```

常用配置：

```text
GITHUB_TOKEN=你的 GitHub token
MIMO_API_KEY=你的 MiMo key
MiMO_BASE_URL=你的 MiMo base_url
OPENAI_API_KEY=备用 OpenAI-compatible key
OPENAI_BASE_URL=备用 OpenAI-compatible base_url
# HTTPS_PROXY=http://127.0.0.1:7890
# HTTP_PROXY=http://127.0.0.1:7890
```

不要把 `.env` 里的 token 发给别人，也不要提交到公开仓库。

## 当前来源

- GitHub Trending：开源项目趋势
- GitHub Releases：重点项目发布
- arXiv：AI、机器学习、NLP、视觉等论文
- Hacker News：技术热点
- GDELT：全球时事新闻，按 AI 政策、芯片、能源、金融、地缘冲突、中美关系等主题池轮换抓取
- RSS：技术博客、研究机构、产品新闻和高质量国际新闻源

## 网页仪表盘能力

- 日期、分类、状态、关键词筛选
- 今日主线和重点排序
- LLM 摘要与本地回退摘要
- 全球时事中文翻译
- 全球时事主题标签：在不改变左侧分类的前提下，用卡片标签展示子主题
- 推荐理由、新鲜度、影响范围、风险提示
- 收藏、忽略、稍后看、归档
- 关注主题和异常趋势提醒
- 来源健康状态、近期趋势、本周沉淀
- 配置中心：来源、偏好、权重、翻译和 LLM 选项

## 常用脚本

```text
start_local_intel.ps1       启动网页和每日调度器
stop_local_intel.ps1        停止网页和每日调度器
status_local_intel.ps1      查看运行状态
install_autostart.ps1       注册开机登录后自动启动
uninstall_autostart.ps1     取消开机自动启动
run_daily.ps1               给 Windows 任务计划使用的单次抓取脚本
```

## 联网自检

如果所有来源都抓取失败，先运行：

```powershell
python -m app.doctor --env .\.env
```

如果看到 SSL/TLS handshake、timeout、connection failed 等错误，通常是当前网络访问这些 HTTPS API 不稳定。可以打开代理或 VPN，也可以在 `.env` 中配置 `HTTPS_PROXY` / `HTTP_PROXY`。

## 当前边界

- 不抓取需要登录或强反爬的网站
- LLM 失败不会中断日报，会回退到本地规则摘要
- 一个来源失败不会中断整份日报，会写入来源健康状态
- 程序只在本机运行，本地网页默认绑定 `127.0.0.1`
