from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from app.models import Item
from app.ranker import select_highlights


CATEGORY_LABELS = {
    "technology": "技术热点",
    "open_source": "开源项目",
    "ai": "AI 与论文",
    "world_news": "全球时事",
    "programming": "编程与工程",
    "general": "其他",
}


def write_reports(
    report_dir: Path,
    run_date: str,
    items: list[Item],
    llm_summary: str,
    stats: dict[str, Any],
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path = report_dir / f"{run_date}.md"
    html_path = report_dir / f"{run_date}.html"
    md_path.write_text(render_markdown(run_date, items, llm_summary, stats), encoding="utf-8")
    html_path.write_text(render_html(run_date, items, llm_summary, stats), encoding="utf-8")
    return md_path, html_path


def render_markdown(run_date: str, items: list[Item], llm_summary: str, stats: dict[str, Any]) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    lines = [f"# Local Intel 日报 - {run_date}", "", f"生成时间：{now}", ""]
    lines.extend(["## 今日重点", ""])
    top_items = select_highlights(items, 5)
    if top_items:
        for item in top_items:
            label = CATEGORY_LABELS.get(item.category, item.category)
            lines.append(f"- [{display_title(item)}]({item.url}) - {label} / {item.source} / rank {item.rank_score:g}")
            original = original_title(item)
            if original and original != display_title(item):
                lines.append(f"  - 原文标题：{original}")
            summary = item.ai_summary or item.top_reason or item.compact_summary(180)
            if summary:
                lines.append(f"  - {summary}")
            if item.why:
                lines.append(f"  - 重要性：{item.why}")
    else:
        lines.append("- 今天没有抓到可用条目。")
    lines.append("")

    if llm_summary:
        lines.extend(["## LLM 摘要", "", llm_summary, ""])

    briefing = stats.get("intel_briefing", {})
    if isinstance(briefing, dict) and briefing.get("headline"):
        lines.extend(["## 情报官日评", ""])
        lines.append(f"**{briefing['headline']}**")
        lines.append("")
        if briefing.get("analysis"):
            lines.append(briefing["analysis"])
            lines.append("")
        if briefing.get("watch_digest"):
            lines.append(f"> 观察雷达：{briefing['watch_digest']}")
            lines.append("")
        model = briefing.get("model", "")
        gen = "LLM" if briefing.get("generation") == "llm" else "本地规则"
        lines.append(f"*生成方式：{gen}{f' · {model}' if model else ''}*")
        lines.append("")

    grouped: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        grouped[item.category].append(item)
    for category in sorted(grouped, key=lambda key: CATEGORY_LABELS.get(key, key)):
        label = CATEGORY_LABELS.get(category, category)
        lines.extend([f"## {label}", ""])
        rows = sorted(grouped[category], key=lambda item: item.rank_score, reverse=True)[:20]
        for item in rows:
            lines.append(f"- [{display_title(item)}]({item.url})")
            original = original_title(item)
            if original and original != display_title(item):
                lines.append(f"  - 原文标题：{original}")
            meta = f"{item.source}"
            if item.published_at:
                meta += f" | {item.published_at}"
            meta += f" | rank: {item.rank_score:g}"
            if item.importance:
                meta += f" | importance: {item.importance}/5"
            lines.append(f"  - {meta}")
            summary = item.ai_summary or item.compact_summary(240)
            if summary:
                lines.append(f"  - {summary}")
            if item.why:
                lines.append(f"  - {item.why}")
        lines.append("")

    lines.extend(["## 抓取状态", ""])
    for source, count in stats.get("source_counts", {}).items():
        lines.append(f"- {source}: {count}")
    errors = stats.get("errors", [])
    if errors:
        lines.extend(["", "### 错误", ""])
        for error in errors:
            lines.append(f"- {error}")
    lines.append("")
    return "\n".join(lines)


def render_html(run_date: str, items: list[Item], llm_summary: str, stats: dict[str, Any]) -> str:
    grouped: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        grouped[item.category].append(item)

    top_html = "".join(render_card(item) for item in select_highlights(items, 5))
    if not top_html:
        top_html = "<p>今天没有抓到可用条目。</p>"

    section_html = []
    for category in sorted(grouped, key=lambda key: CATEGORY_LABELS.get(key, key)):
        label = CATEGORY_LABELS.get(category, category)
        cards = "".join(
            render_card(item)
            for item in sorted(grouped[category], key=lambda item: item.rank_score, reverse=True)[:20]
        )
        section_html.append(f"<section><h2>{escape(label)}</h2><div class='grid'>{cards}</div></section>")

    source_counts = "".join(
        f"<li><strong>{escape(str(source))}</strong>: {count}</li>"
        for source, count in stats.get("source_counts", {}).items()
    )
    errors = "".join(f"<li>{escape(str(error))}</li>" for error in stats.get("errors", []))
    llm_block = f"<section><h2>LLM 摘要</h2><pre>{escape(llm_summary)}</pre></section>" if llm_summary else ""

    briefing = stats.get("intel_briefing", {})
    briefing_block = ""
    if isinstance(briefing, dict) and briefing.get("headline"):
        model = briefing.get("model", "")
        gen = "LLM" if briefing.get("generation") == "llm" else "本地规则"
        watch = f'<p style="color:var(--muted);font-size:14px;">观察雷达：{escape(briefing.get("watch_digest", ""))}</p>' if briefing.get("watch_digest") else ""
        briefing_block = f"""<section style="padding:18px;border:1px solid var(--line);border-radius:10px;background:linear-gradient(135deg,#fff,rgba(15,118,110,0.04));margin-bottom:24px;">
    <h2 style="color:var(--accent);margin-top:0;">情报官日评</h2>
    <p style="font-size:18px;font-weight:700;">{escape(briefing["headline"])}</p>
    {'<p>' + escape(briefing.get("analysis", "")) + '</p>' if briefing.get("analysis") else ""}
    {watch}
    <small style="color:var(--muted);">生成方式：{gen}{f' · {escape(model)}' if model else ''}</small>
  </section>"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local Intel 日报 - {escape(run_date)}</title>
  <style>
    :root {{ color-scheme: light; --bg: #f7f7f4; --ink: #1f2528; --muted: #64707a; --line: #d8ddd8; --accent: #0f766e; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; background: var(--bg); color: var(--ink); line-height: 1.55; }}
    header, main {{ max-width: 1120px; margin: 0 auto; padding: 28px 20px; }}
    header {{ padding-bottom: 8px; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(28px, 5vw, 48px); letter-spacing: 0; }}
    h2 {{ margin: 28px 0 14px; font-size: 22px; letter-spacing: 0; }}
    .muted, .meta {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
    .card {{ min-height: 170px; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    .card a {{ color: var(--ink); text-decoration: none; font-weight: 700; }}
    .card a:hover {{ color: var(--accent); }}
    .why {{ color: var(--muted); font-size: 14px; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }}
    .tags span {{ border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; font-size: 12px; color: var(--muted); }}
    .meta {{ margin-top: 10px; font-size: 13px; }}
    pre {{ white-space: pre-wrap; padding: 16px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    ul {{ padding-left: 20px; }}
  </style>
</head>
<body>
  <header>
    <h1>Local Intel 日报</h1>
    <div class="muted">{escape(run_date)} · {escape(datetime.now().isoformat(timespec="seconds"))}</div>
  </header>
  <main>
    <section><h2>今日重点</h2><div class="grid">{top_html}</div></section>
    {briefing_block}
    {llm_block}
    {''.join(section_html)}
    <section><h2>抓取状态</h2><ul>{source_counts}</ul>{f"<h3>错误</h3><ul>{errors}</ul>" if errors else ""}</section>
  </main>
</body>
</html>
"""


def render_card(item: Item) -> str:
    title = escape(display_title(item))
    original = original_title(item)
    url = escape(item.url)
    label = escape(CATEGORY_LABELS.get(item.category, item.category))
    source = escape(item.source)
    summary = escape(item.ai_summary or item.compact_summary(260))
    why = escape(item.why or item.top_reason)
    published = escape(item.published_at)
    tags = "".join(f"<span>{escape(tag)}</span>" for tag in item.tags[:4])
    importance = f" · importance {item.importance}/5" if item.importance else ""
    return (
        "<article class='card'>"
        f"<a href='{url}' target='_blank' rel='noreferrer'>{title}</a>"
        + (f"<p class='why'>原文标题：{escape(original)}</p>" if original and original != display_title(item) else "")
        + f"<p>{summary}</p>"
        + f"<p class='why'>{why}</p>"
        + f"<div class='tags'>{tags}</div>"
        + f"<div class='meta'>{label} · {source} · rank {item.rank_score:g}{importance}<br>{published}</div>"
        + "</article>"
    )


def display_title(item: Item) -> str:
    value = str(item.raw.get("zh_title") or "").strip()
    return value or item.title


def original_title(item: Item) -> str:
    return str(item.raw.get("original_title") or "").strip()
