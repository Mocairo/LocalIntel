from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from app.db import init_db, latest_report_date
from app.report import CATEGORY_LABELS


BUCKET_LABELS = {
    "must": "必看",
    "scan": "可扫",
    "archive": "归档",
}

READ_LABELS = {
    "unread": "未读",
    "read": "已读",
    "later": "稍后看",
    "archived": "已归档",
}


def build_weekly_report(db_path: Path, report_dir: Path, report_date: str = "") -> dict[str, object]:
    init_db(db_path)
    if not report_date:
        report_date = latest_report_date(db_path)
    if not report_date:
        summary = empty_week_summary()
        write_weekly_files(report_dir, summary)
        return summary

    target = date.fromisoformat(report_date)
    week_start = target - timedelta(days=target.weekday())
    week_end = week_start + timedelta(days=6)
    items = load_week_items(db_path, week_start, week_end)
    clusters = load_week_clusters(db_path, week_start, week_end)
    summary = summarize_week(target, week_start, week_end, items, clusters)
    write_weekly_files(report_dir, summary)
    return summary


def empty_week_summary() -> dict[str, object]:
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    iso_year, iso_week, _ = today.isocalendar()
    return {
        "week_id": f"{iso_year}-W{iso_week:02d}",
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "label": f"{week_start.isoformat()} 至 {week_end.isoformat()}",
        "report_dates": [],
        "item_total": 0,
        "active_total": 0,
        "ignored_total": 0,
        "archived_total": 0,
        "category_counts": [],
        "source_counts": [],
        "bucket_counts": [],
        "read_status_counts": [],
        "daily_counts": [],
        "top_tags": [],
        "top_items": [],
        "top_clusters": [],
    }


def load_week_items(db_path: Path, week_start: date, week_end: date) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                ir.report_date,
                i.hash,
                i.source,
                i.title,
                i.url,
                i.published_at,
                i.summary,
                i.category,
                ir.rank_score,
                i.ai_summary,
                i.why,
                i.importance,
                i.bucket,
                i.tags_json,
                i.top_reason,
                COALESCE(m.favorite, 0) AS favorite,
                COALESCE(m.ignored, 0) AS ignored,
                COALESCE(m.read_status, 'unread') AS read_status
            FROM item_runs ir
            JOIN items i ON i.hash = ir.item_hash
            LEFT JOIN user_marks m ON m.item_hash = i.hash
            WHERE ir.report_date BETWEEN ? AND ?
            ORDER BY ir.report_date DESC, ir.rank_score DESC
            """,
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()
    return [row_to_week_item(row) for row in rows]


def load_week_clusters(db_path: Path, week_start: date, week_end: date) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT report_date, cluster_id, title, category, summary, explanation, score, size
            FROM clusters
            WHERE report_date BETWEEN ? AND ?
            ORDER BY score DESC
            LIMIT 12
            """,
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()
    return [dict(row) for row in rows]


def row_to_week_item(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        tags = json.loads(data.pop("tags_json") or "[]")
    except json.JSONDecodeError:
        tags = []
    data["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
    data["favorite"] = bool(data["favorite"])
    data["ignored"] = bool(data["ignored"])
    return data


def summarize_week(
    target: date,
    week_start: date,
    week_end: date,
    rows: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> dict[str, object]:
    iso_year, iso_week, _ = target.isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"
    latest_by_hash: dict[str, dict[str, Any]] = {}
    for row in rows:
        item_hash = str(row.get("hash") or "")
        existing = latest_by_hash.get(item_hash)
        if not existing or float(row.get("rank_score") or 0) > float(existing.get("rank_score") or 0):
            latest_by_hash[item_hash] = row

    unique_items = list(latest_by_hash.values())
    active_items = [
        item
        for item in unique_items
        if not item.get("ignored") and item.get("read_status") != "archived"
    ]
    active_run_rows = [
        row
        for row in rows
        if not row.get("ignored") and row.get("read_status") != "archived"
    ]

    category_counts = count_rows(active_items, "category", CATEGORY_LABELS)
    source_counts = count_rows(active_items, "source")
    bucket_counts = count_rows(active_items, "bucket", BUCKET_LABELS)
    read_status_counts = count_rows(
        [item for item in unique_items if not item.get("ignored")],
        "read_status",
        READ_LABELS,
    )
    daily_counts = [
        {"date": key, "count": value}
        for key, value in sorted(Counter(str(row.get("report_date") or "") for row in active_run_rows).items())
        if key
    ]

    tag_counter: Counter[str] = Counter()
    for item in active_items:
        tag_counter.update(str(tag) for tag in item.get("tags", [])[:8])
    top_tags = [{"tag": tag, "count": count} for tag, count in tag_counter.most_common(12)]
    top_items = sorted(active_items, key=lambda item: float(item.get("rank_score") or 0), reverse=True)[:12]

    return {
        "week_id": week_id,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "label": f"{week_start.isoformat()} 至 {week_end.isoformat()}",
        "report_dates": sorted({str(row.get("report_date") or "") for row in rows if row.get("report_date")}),
        "item_total": len(unique_items),
        "active_total": len(active_items),
        "ignored_total": sum(1 for item in unique_items if item.get("ignored")),
        "archived_total": sum(1 for item in unique_items if item.get("read_status") == "archived"),
        "category_counts": category_counts,
        "source_counts": source_counts,
        "bucket_counts": bucket_counts,
        "read_status_counts": read_status_counts,
        "daily_counts": daily_counts,
        "top_tags": top_tags,
        "top_items": top_items,
        "top_clusters": clusters[:8],
    }


def count_rows(rows: list[dict[str, Any]], key: str, labels: dict[str, str] | None = None) -> list[dict[str, object]]:
    counter = Counter(str(row.get(key) or "unknown") for row in rows)
    result = []
    for name, count in counter.most_common():
        result.append({"key": name, "label": (labels or {}).get(name, name), "count": count})
    return result


def write_weekly_files(report_dir: Path, summary: dict[str, object]) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    week_id = str(summary["week_id"])
    md_path = report_dir / f"weekly-{week_id}.md"
    html_path = report_dir / f"weekly-{week_id}.html"
    summary["markdown_path"] = str(md_path)
    summary["html_path"] = str(html_path)
    summary["markdown_url"] = f"/reports/{md_path.name}"
    summary["html_url"] = f"/reports/{html_path.name}"
    md_path.write_text(render_weekly_markdown(summary), encoding="utf-8")
    html_path.write_text(render_weekly_html(summary), encoding="utf-8")
    return md_path, html_path


def render_weekly_markdown(summary: dict[str, object]) -> str:
    lines = [
        f"# Local Intel 周报 - {summary['week_id']}",
        "",
        f"周期：{summary['label']}",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 本周概览",
        "",
        f"- 有效条目：{summary['active_total']}",
        f"- 覆盖日报：{len(summary.get('report_dates', []))} 天",
        f"- 已归档：{summary['archived_total']}",
        f"- 已忽略：{summary['ignored_total']}",
        "",
    ]
    append_count_section(lines, "分类分布", summary.get("category_counts", []))
    append_count_section(lines, "来源分布", summary.get("source_counts", []))
    append_count_section(lines, "阅读状态", summary.get("read_status_counts", []))

    top_tags = summary.get("top_tags", [])
    if isinstance(top_tags, list) and top_tags:
        lines.extend(["## 本周热词", ""])
        for row in top_tags:
            lines.append(f"- {row.get('tag')}: {row.get('count')}")
        lines.append("")

    lines.extend(["## 本周最值得回看", ""])
    top_items = summary.get("top_items", [])
    if isinstance(top_items, list) and top_items:
        for item in top_items:
            title = item.get("title") or "未命名"
            url = item.get("url") or "#"
            label = CATEGORY_LABELS.get(str(item.get("category") or ""), str(item.get("category") or "其他"))
            lines.append(f"- [{title}]({url}) - {label} / {item.get('source')} / rank {float(item.get('rank_score') or 0):g}")
            summary_text = item.get("ai_summary") or item.get("summary") or item.get("top_reason") or ""
            if summary_text:
                lines.append(f"  - {summary_text}")
    else:
        lines.append("- 本周还没有可沉淀的条目。")
    lines.append("")
    return "\n".join(lines)


def append_count_section(lines: list[str], title: str, rows: object) -> None:
    if not isinstance(rows, list) or not rows:
        return
    lines.extend([f"## {title}", ""])
    for row in rows:
        if isinstance(row, dict):
            lines.append(f"- {row.get('label')}: {row.get('count')}")
    lines.append("")


def render_weekly_html(summary: dict[str, object]) -> str:
    category_rows = render_count_rows(summary.get("category_counts", []))
    source_rows = render_count_rows(summary.get("source_counts", []))
    read_rows = render_count_rows(summary.get("read_status_counts", []))
    tag_rows = render_tags(summary.get("top_tags", []))
    daily_rows = render_daily_rows(summary.get("daily_counts", []))
    top_items = summary.get("top_items", [])
    top_cards = "".join(render_weekly_card(item) for item in top_items) if isinstance(top_items, list) else ""
    cluster_rows = render_cluster_rows(summary.get("top_clusters", []))
    if not top_cards:
        top_cards = "<div class='empty'>本周还没有可沉淀的条目。</div>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local Intel 周报 - {escape(str(summary['week_id']))}</title>
  <style>
    :root {{ color-scheme: light; --bg: #f4f6f5; --panel: #fff; --ink: #172026; --muted: #62717d; --line: #d7dddc; --teal: #0f766e; --blue: #2557a7; --amber: #9a650d; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: linear-gradient(180deg, #eef3f2 0, #f4f6f5 260px), var(--bg); color: var(--ink); font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; line-height: 1.55; }}
    header, main {{ max-width: 1180px; margin: 0 auto; padding: 26px 20px; }}
    header {{ padding-bottom: 8px; }}
    h1 {{ margin: 0 0 6px; font-size: clamp(30px, 5vw, 52px); letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; letter-spacing: 0; }}
    a {{ color: var(--ink); text-decoration: none; }}
    a:hover {{ color: var(--teal); text-decoration: underline; text-underline-offset: 3px; }}
    .muted {{ color: var(--muted); }}
    .stats, .panels, .grid {{ display: grid; gap: 12px; }}
    .stats {{ grid-template-columns: repeat(4, minmax(140px, 1fr)); margin-top: 20px; }}
    .stat, .panel, .card {{ border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }}
    .stat {{ min-height: 92px; padding: 15px; }}
    .stat strong {{ display: block; font-size: 28px; line-height: 1; }}
    .stat span, .meta, .small {{ color: var(--muted); font-size: 13px; }}
    .panels {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .panel {{ padding: 14px; }}
    .panel h2 {{ margin-top: 0; font-size: 16px; }}
    .row {{ display: grid; grid-template-columns: 1fr auto; gap: 10px; padding: 6px 0; border-bottom: 1px solid #edf0ef; }}
    .row:last-child {{ border-bottom: 0; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 7px; }}
    .tags span {{ padding: 4px 9px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); background: #fbfcfb; font-size: 12px; }}
    .grid {{ grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
    .card {{ min-height: 188px; padding: 15px; display: grid; gap: 9px; align-content: start; }}
    .card h3 {{ margin: 0; font-size: 17px; line-height: 1.35; }}
    .card p {{ margin: 0; }}
    .summary {{ font-size: 14px; }}
    .empty {{ padding: 18px; color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; }}
    @media (max-width: 800px) {{ .stats, .panels {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <div class="muted">Local Intel 周报</div>
    <h1>{escape(str(summary['week_id']))}</h1>
    <div class="muted">{escape(str(summary['label']))} · 生成时间 {escape(datetime.now().isoformat(timespec="seconds"))}</div>
    <section class="stats">
      <div class="stat"><strong>{escape(str(summary['active_total']))}</strong><span>有效条目</span></div>
      <div class="stat"><strong>{escape(str(len(summary.get('report_dates', []))))}</strong><span>覆盖日报</span></div>
      <div class="stat"><strong>{escape(str(find_count(summary.get('bucket_counts', []), 'must')))}</strong><span>必看条目</span></div>
      <div class="stat"><strong>{escape(str(find_count(summary.get('read_status_counts', []), 'unread')))}</strong><span>未读条目</span></div>
    </section>
  </header>
  <main>
    <section class="panels">
      <div class="panel"><h2>分类分布</h2>{category_rows}</div>
      <div class="panel"><h2>来源分布</h2>{source_rows}</div>
      <div class="panel"><h2>阅读状态</h2>{read_rows}</div>
    </section>
    <section class="panel" style="margin-top:12px"><h2>本周热词</h2>{tag_rows}</section>
    <section class="panel" style="margin-top:12px"><h2>每日沉淀</h2>{daily_rows}</section>
    <section><h2>本周最值得回看</h2><div class="grid">{top_cards}</div></section>
    <section class="panel"><h2>高分主线</h2>{cluster_rows}</section>
  </main>
</body>
</html>
"""


def render_count_rows(rows: object) -> str:
    if not isinstance(rows, list) or not rows:
        return "<div class='empty'>暂无数据</div>"
    html = []
    for row in rows[:10]:
        if isinstance(row, dict):
            html.append(f"<div class='row'><span>{escape(str(row.get('label') or row.get('key') or ''))}</span><b>{escape(str(row.get('count') or 0))}</b></div>")
    return "".join(html)


def render_tags(rows: object) -> str:
    if not isinstance(rows, list) or not rows:
        return "<div class='empty'>暂无热词</div>"
    tags = []
    for row in rows[:14]:
        if isinstance(row, dict):
            tags.append(f"<span>{escape(str(row.get('tag') or ''))} · {escape(str(row.get('count') or 0))}</span>")
    return f"<div class='tags'>{''.join(tags)}</div>"


def render_daily_rows(rows: object) -> str:
    if not isinstance(rows, list) or not rows:
        return "<div class='empty'>暂无日报数据</div>"
    return "".join(
        f"<div class='row'><span>{escape(str(row.get('date') or ''))}</span><b>{escape(str(row.get('count') or 0))} 条</b></div>"
        for row in rows
        if isinstance(row, dict)
    )


def render_cluster_rows(rows: object) -> str:
    if not isinstance(rows, list) or not rows:
        return "<div class='empty'>暂无主线</div>"
    html = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        label = CATEGORY_LABELS.get(str(row.get("category") or ""), str(row.get("category") or "其他"))
        text = row.get("explanation") or row.get("summary") or ""
        html.append(
            "<div class='row'>"
            f"<span><b>{escape(str(row.get('title') or ''))}</b><br><span class='small'>{escape(label)} · {escape(str(row.get('report_date') or ''))} · {escape(str(row.get('size') or 0))} 条</span><br>{escape(str(text))}</span>"
            f"<b>{float(row.get('score') or 0):.1f}</b>"
            "</div>"
        )
    return "".join(html)


def render_weekly_card(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    title = escape(str(item.get("title") or "未命名"))
    url = escape(str(item.get("url") or "#"))
    label = escape(CATEGORY_LABELS.get(str(item.get("category") or ""), str(item.get("category") or "其他")))
    summary = escape(str(item.get("ai_summary") or item.get("summary") or item.get("top_reason") or "暂无摘要"))
    source = escape(str(item.get("source") or ""))
    bucket = escape(BUCKET_LABELS.get(str(item.get("bucket") or ""), str(item.get("bucket") or "")))
    read_status = escape(READ_LABELS.get(str(item.get("read_status") or ""), str(item.get("read_status") or "")))
    rank = float(item.get("rank_score") or 0)
    tags = "".join(f"<span>{escape(str(tag))}</span>" for tag in item.get("tags", [])[:5])
    return (
        "<article class='card'>"
        f"<h3><a href='{url}' target='_blank' rel='noreferrer'>{title}</a></h3>"
        f"<p class='summary'>{summary}</p>"
        f"<div class='tags'>{tags}</div>"
        f"<div class='meta'>{label} · {source} · {bucket} · {read_status} · rank {rank:.1f}<br>{escape(str(item.get('published_at') or ''))}</div>"
        "</article>"
    )


def find_count(rows: object, key: str) -> int:
    if not isinstance(rows, list):
        return 0
    for row in rows:
        if isinstance(row, dict) and row.get("key") == key:
            return int(row.get("count") or 0)
    return 0
