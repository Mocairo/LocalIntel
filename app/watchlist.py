from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.dedupe import item_hash
from app.db import record_llm_job, record_watch_radar
from app.models import Item
from app.summarizer import env_value, parse_json_object, request_summary, response_content, token_budgets


@dataclass(slots=True)
class WatchTarget:
    id: str
    name: str
    type: str
    keywords: list[str]
    description: str = ""
    enabled: bool = True


def build_watch_radar(settings: Settings, items: list[Item], report_date: str, limit: int = 6) -> list[dict[str, object]]:
    targets = load_watchlist(settings.app_path("interests_file"))
    db_path = settings.app_path("data_dir") / "intel.sqlite"
    if not targets:
        record_watch_radar(db_path, report_date, [])
        return []

    rows = build_local_watch_radar(targets, items, report_date, limit=limit)
    rows = refine_watch_radar_with_llm(settings, rows, report_date)
    record_watch_radar(db_path, report_date, rows)
    return rows


def load_watchlist(path: Path) -> list[WatchTarget]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        values = tomllib.load(fh)
    rows = values.get("watchlist", [])
    if not isinstance(rows, list):
        return []
    targets: list[WatchTarget] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("enabled", True) is False:
            continue
        keywords = clean_list(row.get("keywords"))
        target_id = clean_text(row.get("id"))
        name = clean_text(row.get("name"))
        if not target_id or not name or not keywords:
            continue
        targets.append(
            WatchTarget(
                id=target_id[:80],
                name=name[:120],
                type=clean_text(row.get("type")) or "topic",
                keywords=keywords,
                description=clean_text(row.get("description")),
                enabled=True,
            )
        )
    return targets


def refine_watch_radar_with_llm(
    settings: Settings, rows: list[dict[str, object]], report_date: str
) -> list[dict[str, object]]:
    section = settings.section("llm")
    db_path = settings.app_path("data_dir") / "intel.sqlite"
    model = str(section.get("model", "mimo-v2.5-pro"))
    if not rows or not section.get("enabled", False):
        return rows

    api_key_env = str(section.get("api_key_env", "MIMO_API_KEY"))
    fallback_api_key_env = str(section.get("fallback_api_key_env", "OPENAI_API_KEY"))
    api_key = env_value(api_key_env, fallback_api_key_env)
    if not api_key:
        record_llm_job(db_path, report_date, "watch_radar", "skipped", model, len(rows), f"{api_key_env} is not set")
        return rows

    base_url_env = str(section.get("base_url_env", "MiMO_BASE_URL"))
    fallback_base_url_env = str(section.get("fallback_base_url_env", "OPENAI_BASE_URL"))
    base_url = env_value(base_url_env, fallback_base_url_env) or "https://api.openai.com/v1"
    model_candidates = [str(row) for row in section.get("model_candidates", []) if str(row).strip()]
    if model not in model_candidates:
        model_candidates.insert(0, model)
    max_tokens = int(section.get("max_tokens", 8000))
    timeout_seconds = int(section.get("timeout_seconds", 90))
    temperature = float(section.get("temperature", 1.0))
    top_p = float(section.get("top_p", 0.95))
    prompt = (
        "你是个人情报系统的观察雷达判断器。只根据输入 JSON 判断每个观察对象今天是否有明显动向。"
        "直接输出 JSON，不要 Markdown。"
        '格式：{"radar":[{"target_id":"id","status":"active|quiet",'
        '"summary":"中文一句话","action":"立即看|持续观察|暂不处理","confidence":0.0}]}。'
        "不得新增 target_id，不得编造输入中没有的信息。"
    )

    response: object = {}
    used_model = ""
    last_error = ""
    for candidate_model in model_candidates:
        try:
            for token_budget in token_budgets(max_tokens):
                response = request_summary(
                    base_url,
                    api_key,
                    candidate_model,
                    prompt,
                    rows,
                    temperature,
                    top_p,
                    token_budget,
                    timeout_seconds,
                )
                content, finish_reason, _ = response_content(response)
                if content:
                    used_model = candidate_model
                    break
                last_error = f"empty watch radar response; finish_reason={finish_reason or 'unknown'}"
                if finish_reason != "length":
                    break
            if used_model:
                break
        except Exception as exc:
            last_error = str(exc)
            if "Not supported model" not in last_error and "empty" not in last_error.casefold():
                break
            continue

    if not used_model:
        record_llm_job(db_path, report_date, "watch_radar", "failed", model, len(rows), last_error)
        return rows

    content, _, _ = response_content(response)
    parsed = parse_json_object(content)
    if not parsed:
        record_llm_job(db_path, report_date, "watch_radar", "fallback", used_model, len(rows), "non-json content")
        return rows

    merged = merge_llm_radar(rows, parsed)
    record_llm_job(db_path, report_date, "watch_radar", "ok", used_model, len(rows), "")
    return merged


def merge_llm_radar(rows: list[dict[str, object]], parsed: dict[str, object]) -> list[dict[str, object]]:
    incoming = parsed.get("radar", [])
    if not isinstance(incoming, list):
        return rows
    by_id = {str(row.get("target_id") or ""): row for row in rows}
    for row in incoming:
        if not isinstance(row, dict):
            continue
        target_id = str(row.get("target_id") or "")
        target = by_id.get(target_id)
        if not target:
            continue
        status = clean_text(row.get("status"))
        if status in {"active", "quiet"}:
            target["status"] = status
        summary = clean_text(row.get("summary"))
        if summary:
            target["summary"] = summary[:500]
            target["generation"] = "llm"
        action = clean_text(row.get("action"))
        if action:
            target["action"] = action[:20]
        target["confidence"] = clamp_float(row.get("confidence"), 0.0, 1.0)
    rows.sort(key=lambda row: (row["status"] == "active", float(row["score"] or 0), int(row["match_count"] or 0)), reverse=True)
    return rows


def build_local_watch_radar(
    targets: list[WatchTarget], items: list[Item], report_date: str, limit: int = 6
) -> list[dict[str, object]]:
    rows = [radar_row(target, items, report_date) for target in targets]
    rows.sort(key=lambda row: (row["status"] == "active", float(row["score"] or 0), int(row["match_count"] or 0)), reverse=True)
    return rows[:limit]


def radar_row(target: WatchTarget, items: list[Item], report_date: str) -> dict[str, object]:
    matches = [item for item in items if target_matches(target, item)]
    matches.sort(key=lambda item: item.rank_score, reverse=True)
    top = matches[0] if matches else None
    if top:
        score = round(float(top.rank_score or 0), 2)
        summary = f"命中 {len(matches)} 条相关情报，代表条目：{top.title}"
        return {
            "report_date": report_date,
            "target_id": target.id,
            "name": target.name,
            "type": target.type,
            "status": "active",
            "summary": summary,
            "action": "立即看" if score >= 85 else "持续观察",
            "generation": "local_rule",
            "confidence": min(1.0, max(0.35, score / 100)),
            "match_count": len(matches),
            "item_hash": item_hash(top),
            "item_title": top.title,
            "source": top.source,
            "url": top.url,
            "score": score,
        }
    return {
        "report_date": report_date,
        "target_id": target.id,
        "name": target.name,
        "type": target.type,
        "status": "quiet",
        "summary": quiet_summary(target),
        "action": "持续观察",
        "generation": "local_rule",
        "confidence": 0.0,
        "match_count": 0,
        "item_hash": "",
        "item_title": "",
        "source": "",
        "url": "",
        "score": 0.0,
    }


def quiet_summary(target: WatchTarget) -> str:
    keywords = "、".join(target.keywords[:4])
    description = f"观察范围：{target.description}。" if target.description else ""
    return f"今日未命中相关证据，系统仍在观察该对象。{description}建议关键词：{keywords}。"


def target_matches(target: WatchTarget, item: Item) -> bool:
    text = " ".join(
        [
            item.title,
            item.summary,
            item.content,
            item.ai_summary,
            item.why,
            item.top_reason,
            item.source,
            item.category,
            " ".join(item.tags),
            json.dumps(item.raw, ensure_ascii=False),
        ]
    ).casefold()
    return any(keyword_matches(keyword, text) for keyword in target.keywords)


def keyword_matches(keyword: str, text: str) -> bool:
    needle = keyword.casefold().strip()
    if not needle:
        return False
    if len(needle) <= 3 and needle.isascii() and needle.replace("-", "").isalnum():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text))
    return needle in text


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(row) for row in value if clean_text(row)]


def clamp_float(value: object, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, number))
