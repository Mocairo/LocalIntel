from __future__ import annotations

import json

from app.config import Settings
from app.dedupe import item_hash
from app.db import record_llm_alerts, record_llm_job
from app.models import Item
from app.summarizer import (
    env_value,
    parse_json_object,
    request_summary,
    response_content,
    token_budgets,
)


DEFAULT_ALERT_LIMIT = 6


def normalize_llm_alerts(response: dict[str, object], limit: int = DEFAULT_ALERT_LIMIT) -> list[dict[str, object]]:
    rows = response.get("alerts", [])
    if not isinstance(rows, list):
        return []

    alerts: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_hash_value = str(row.get("item_hash") or "").strip()
        if not item_hash_value or item_hash_value in seen:
            continue
        seen.add(item_hash_value)
        alerts.append(
            {
                "item_hash": item_hash_value,
                "kind": clean_text(row.get("kind"), "llm_watch")[:40],
                "title": clean_text(row.get("title"), "模型判断"),
                "detail": clean_text(row.get("detail"), "大模型判断该条目值得关注。"),
                "action": clean_text(row.get("action"), "观察"),
                "confidence": clamp_float(row.get("confidence"), 0.0, 1.0),
            }
        )
        if len(alerts) >= limit:
            break
    return alerts


def build_llm_alerts(
    settings: Settings, items: list[Item], report_date: str, limit: int = DEFAULT_ALERT_LIMIT
) -> list[dict[str, object]]:
    section = settings.section("llm")
    if not section.get("enabled", False):
        return []

    db_path = settings.app_path("data_dir") / "intel.sqlite"
    model = str(section.get("model", "mimo-v2.5"))
    api_key_env = str(section.get("api_key_env", "MIMO_API_KEY"))
    fallback_api_key_env = str(section.get("fallback_api_key_env", "OPENAI_API_KEY"))
    api_key = env_value(api_key_env, fallback_api_key_env)
    if not api_key:
        record_llm_alerts(db_path, report_date, [])
        record_llm_job(db_path, report_date, "alert_triage", "skipped", model, 0, f"{api_key_env} is not set")
        return []

    candidates = alert_candidates(items, limit=20)
    if not candidates:
        record_llm_alerts(db_path, report_date, [])
        return []

    base_url_env = str(section.get("base_url_env", "MiMO_BASE_URL"))
    fallback_base_url_env = str(section.get("fallback_base_url_env", "OPENAI_BASE_URL"))
    base_url = env_value(base_url_env, fallback_base_url_env) or "https://api.openai.com/v1"
    model_candidates = [str(row) for row in section.get("model_candidates", []) if str(row).strip()]
    if model not in model_candidates:
        model_candidates.insert(0, model)
    max_tokens = int(section.get("max_tokens", 4000))
    timeout_seconds = int(section.get("timeout_seconds", 90))
    temperature = float(section.get("temperature", 1.0))
    top_p = float(section.get("top_p", 0.95))

    prompt = (
        "你是个人情报工作台的监控提醒判断器。只根据输入候选条目判断哪些值得提醒。"
        "直接输出 JSON，不要 Markdown。"
        '格式：{"alerts":[{"item_hash":"hash","kind":"llm_watch","title":"短标题",'
        '"detail":"为什么值得提醒","action":"立即看|稍后看|观察","confidence":0.0}]}。'
        f"最多输出 {limit} 条。没有值得提醒的条目时输出空 alerts。"
    )
    payload = [
        {
            "item_hash": item_hash(item),
            "source": item.source,
            "category": item.category,
            "title": item.title,
            "summary": item.ai_summary or item.compact_summary(220),
            "rank_score": round(float(item.rank_score or 0), 2),
            "importance": int(item.importance or 0),
            "bucket": item.bucket,
            "tags": item.tags,
            "top_reason": item.top_reason,
        }
        for item in candidates
    ]

    last_error = ""
    used_model = ""
    response: object = {}
    for candidate_model in model_candidates:
        try:
            for token_budget in token_budgets(max_tokens):
                response = request_summary(
                    base_url,
                    api_key,
                    candidate_model,
                    prompt,
                    payload,
                    temperature,
                    top_p,
                    token_budget,
                    timeout_seconds,
                )
                content, finish_reason, _ = response_content(response)
                if content:
                    used_model = candidate_model
                    break
                last_error = f"empty alert response; finish_reason={finish_reason or 'unknown'}"
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
        record_llm_alerts(db_path, report_date, [])
        record_llm_job(db_path, report_date, "alert_triage", "failed", model, len(candidates), last_error)
        return []

    content, _, _ = response_content(response)
    parsed = parse_json_object(content)
    if not parsed:
        record_llm_alerts(db_path, report_date, [])
        record_llm_job(db_path, report_date, "alert_triage", "fallback", used_model, len(candidates), "non-json content")
        return []

    alerts = enrich_alert_rows(normalize_llm_alerts(parsed, limit=limit), candidates)
    record_llm_alerts(db_path, report_date, alerts)
    record_llm_job(db_path, report_date, "alert_triage", "ok", used_model, len(candidates), "")
    return alerts


def alert_candidates(items: list[Item], limit: int = 20) -> list[Item]:
    ranked = sorted(items, key=lambda item: item.rank_score, reverse=True)
    candidates = [item for item in ranked if item.bucket == "must" or item.importance >= 4 or item.tags]
    if len(candidates) < limit:
        existing = {item_hash(item) for item in candidates}
        candidates.extend(item for item in ranked if item_hash(item) not in existing)
    return candidates[:limit]


def enrich_alert_rows(alerts: list[dict[str, object]], items: list[Item]) -> list[dict[str, object]]:
    by_hash = {item_hash(item): item for item in items}
    enriched: list[dict[str, object]] = []
    for alert in alerts:
        item = by_hash.get(str(alert.get("item_hash") or ""))
        if not item:
            continue
        enriched.append(
            {
                **alert,
                "item_title": item.title,
                "source": item.source,
                "url": item.url,
                "score": float(item.rank_score or 0),
            }
        )
    return enriched


def clean_text(value: object, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def clamp_float(value: object, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, number))
