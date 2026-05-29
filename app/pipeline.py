from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.alerts import build_llm_alerts
from app.config import Settings, load_settings
from app.clusters import build_clusters
from app.db import record_clusters, record_run, save_items
from app.dedupe import dedupe_items
from app.feedback import apply_feedback_scores
from app.preferences import load_preferences
from app.ranker import rank_items
from app.report import write_reports
from app.sources import fetch_all
from app.summarizer import build_llm_summary
from app.translator import translate_world_news
from app.triage import assign_buckets
from app.watchlist import build_watch_radar
from app.weekly import build_weekly_report


ProgressCallback = Callable[[dict[str, Any]], None]


def parse_run_date(value: str, timezone_name: str) -> date:
    if value == "today":
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        return datetime.now(tz).date()
    return date.fromisoformat(value)


def emit_progress(progress: ProgressCallback | None, stage: str, message: str, percent: int, status: str = "running") -> None:
    if progress:
        progress({"stage": stage, "message": message, "percent": percent, "status": status})


def run_pipeline(
    config_path: str | Path = "config.toml",
    env_path: str | Path = ".env",
    run_date: str = "today",
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    settings = load_settings(config_path, env_path)
    timezone_name = str(settings.section("app").get("timezone", "UTC"))
    day = parse_run_date(run_date, timezone_name)

    emit_progress(progress, "starting", "准备开始抓取", 3)
    raw_items, stats = fetch_all(settings, day, progress)
    emit_progress(progress, "rank", "正在去重和排序", 50)
    preferences = load_preferences(settings.app_path("interests_file"))
    db_path = settings.app_path("data_dir") / "intel.sqlite"
    items = apply_feedback_scores(db_path, rank_items(dedupe_items(raw_items), preferences, day))

    stats["raw_total"] = len(raw_items)
    stats["deduped_total"] = len(items)
    emit_progress(progress, "translate", "正在翻译全球时事", 60)
    translation_error = translate_world_news(settings, items)
    if translation_error:
        stats.setdefault("errors", []).append(f"translation: {translation_error}")

    emit_progress(progress, "llm", "正在生成 LLM 摘要和重点解释", 72)
    llm_summary = build_llm_summary(settings, items, day.isoformat())
    emit_progress(progress, "triage", "正在划分必看、可扫和归档", 82)
    items = sorted(items, key=lambda item: item.rank_score, reverse=True)
    assign_buckets(items)
    emit_progress(progress, "clusters", "正在生成今日主线", 88)
    clusters = build_clusters(items)
    stats["cluster_count"] = len(clusters)
    emit_progress(progress, "save", "正在写入本地数据库", 92)
    saved = save_items(db_path, items)
    stats["inserted"] = saved
    record_run(db_path, day.isoformat(), items, stats, llm_summary)
    record_clusters(db_path, day.isoformat(), clusters)
    emit_progress(progress, "alerts", "正在判断高价值信号和观察雷达", 96)
    build_llm_alerts(settings, items, day.isoformat())
    build_watch_radar(settings, items, day.isoformat())
    emit_progress(progress, "report", "正在生成报告文件", 98)
    write_run_log(settings, day.isoformat(), stats, llm_summary)
    md_path, html_path = write_reports(settings.app_path("report_dir"), day.isoformat(), items, llm_summary, stats)
    weekly = build_weekly_report(db_path, settings.app_path("report_dir"), day.isoformat())
    emit_progress(progress, "done", "重跑完成", 100, "ok")
    return {
        "date": day.isoformat(),
        "raw_total": len(raw_items),
        "deduped_total": len(items),
        "inserted": saved,
        "db_path": str(db_path),
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "weekly_html_path": str(weekly.get("html_path", "")),
        "errors": stats.get("errors", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Local Intel daily report.")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    parser.add_argument("--date", default="today", help="Report date: today or YYYY-MM-DD")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM summarization for this run")
    args = parser.parse_args()

    settings = load_settings(args.config, args.env)
    if args.no_llm:
        settings.section("llm")["enabled"] = False

    timezone_name = str(settings.section("app").get("timezone", "UTC"))
    day = parse_run_date(args.date, timezone_name)
    raw_items, stats = fetch_all(settings, day)
    preferences = load_preferences(settings.app_path("interests_file"))
    db_path = settings.app_path("data_dir") / "intel.sqlite"
    items = apply_feedback_scores(db_path, rank_items(dedupe_items(raw_items), preferences, day))
    stats["raw_total"] = len(raw_items)
    stats["deduped_total"] = len(items)
    translation_error = translate_world_news(settings, items)
    if translation_error:
        stats.setdefault("errors", []).append(f"translation: {translation_error}")
    llm_summary = build_llm_summary(settings, items, day.isoformat())
    items = sorted(items, key=lambda item: item.rank_score, reverse=True)
    assign_buckets(items)
    clusters = build_clusters(items)
    stats["cluster_count"] = len(clusters)
    saved = save_items(db_path, items)
    stats["inserted"] = saved
    record_run(db_path, day.isoformat(), items, stats, llm_summary)
    record_clusters(db_path, day.isoformat(), clusters)
    build_llm_alerts(settings, items, day.isoformat())
    build_watch_radar(settings, items, day.isoformat())
    write_run_log(settings, day.isoformat(), stats, llm_summary)
    md_path, html_path = write_reports(settings.app_path("report_dir"), day.isoformat(), items, llm_summary, stats)
    weekly = build_weekly_report(db_path, settings.app_path("report_dir"), day.isoformat())

    print(f"Date: {day.isoformat()}")
    print(f"Fetched: {len(raw_items)}")
    print(f"After dedupe: {len(items)}")
    print(f"Saved into SQLite: {saved}")
    print(f"SQLite: {db_path}")
    print(f"Markdown: {md_path}")
    print(f"HTML: {html_path}")
    print(f"Weekly HTML: {weekly.get('html_path', '')}")
    errors = stats.get("errors", [])
    if errors:
        print("Errors:")
        for error in errors:
            print(f"- {error}")

def write_run_log(settings: Settings, report_date: str, stats: dict[str, object], llm_summary: str) -> None:
    log_dir = settings.app_path("log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report_date": report_date,
        "raw_total": stats.get("raw_total", 0),
        "deduped_total": stats.get("deduped_total", 0),
        "inserted": stats.get("inserted", 0),
        "source_counts": stats.get("source_counts", {}),
        "errors": stats.get("errors", []),
        "llm_summary_length": len(llm_summary),
    }
    with (log_dir / f"{report_date}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
