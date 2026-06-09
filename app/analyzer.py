"""情报官日评引擎 — 从聚合到分析的核心模块。

计算主线趋势信号（新信号/升温/持续/冷却），调用 LLM 生成战略级情报分析。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import (
    init_db,
    load_historical_cluster_keywords,
    load_watch_radar,
    record_llm_job,
    save_intel_briefing,
)
from app.models import Item
from app.summarizer import (
    chat_url,
    configured_model,
    configured_model_candidates,
    env_value,
    parse_json_object,
    request_summary,
    response_content,
    token_budgets,
)

SYSTEM_PROMPT = (
    "你是个人情报工作台的首席分析师。根据今日所有主线、观察雷达和历史趋势，"
    "生成一份战略级情报日评。直接输出 JSON，不要 Markdown。"
    "格式：{"
    '"headline":"一句话总结今天最重要的信号，20字以内",'
    '"analysis":"200字以内的战略分析，连接主线、解释意义、指出潜在影响",'
    '"signals":[{"cluster_id":"主线ID","trend":"new|rising|sustained|fading","note":"一句话趋势说明"}],'
    '"watch_digest":"观察雷达要点，50字以内"'
    "}。"
)

STOPWORDS = frozenset({
    "about", "above", "after", "again", "also", "been", "before",
    "being", "between", "could", "does", "doing", "down", "each",
    "from", "further", "have", "here", "just", "like", "more",
    "most", "much", "must", "need", "only", "other", "over", "same",
    "should", "since", "some", "such", "than", "that", "their",
    "them", "then", "there", "these", "they", "this", "those",
    "through", "under", "very", "were", "what", "when", "where",
    "which", "while", "will", "with", "your",
})

TREND_LABELS = {
    "new": "新信号",
    "rising": "升温",
    "sustained": "持续",
    "fading": "冷却",
}


def text_tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.casefold())
        if token not in STOPWORDS and len(token) > 3
    }
    if not tokens:
        tokens = {token for token in re.findall(r"[一-鿿]{2,}", text) if len(token) >= 2}
    return set(list(tokens)[:30])


def compute_trend_signals(
    today_clusters: list[dict[str, Any]],
    history: dict[str, list[dict[str, Any]]],
    report_date: str,
) -> list[dict[str, str]]:
    history_token_sets: dict[str, set[tuple[str, str]]] = {}
    for date_key, day_clusters in history.items():
        for cluster in day_clusters:
            key = cluster.get("cluster_id", "")
            text = f"{cluster.get('title', '')} {cluster.get('explanation', '')}"
            tokens = text_tokens(text)
            history_token_sets.setdefault(key, set())
            for t in tokens:
                history_token_sets[key].add((t, date_key))

    global_tokens_by_date: dict[str, set[str]] = {}
    for date_key, day_clusters in history.items():
        for cluster in day_clusters:
            text = f"{cluster.get('title', '')} {cluster.get('explanation', '')}"
            tokens = text_tokens(text)
            global_tokens_by_date.setdefault(date_key, set()).update(tokens)

    all_history_tokens: dict[str, list[str]] = {}
    for date_key, tokens in global_tokens_by_date.items():
        for token in tokens:
            all_history_tokens.setdefault(token, []).append(date_key)

    recent_dates = sorted(global_tokens_by_date.keys(), reverse=True)[:3]
    recent_tokens: set[str] = set()
    for d in recent_dates:
        recent_tokens.update(global_tokens_by_date.get(d, set()))

    older_dates = sorted(global_tokens_by_date.keys(), reverse=True)[3:7]
    older_tokens: set[str] = set()
    for d in older_dates:
        older_tokens.update(global_tokens_by_date.get(d, set()))

    signals: list[dict[str, str]] = []
    for cluster in today_clusters:
        cluster_id = cluster.get("cluster_id", "")
        title = cluster.get("title", "")
        text = f"{title} {cluster.get('explanation', '')}"
        today_tokens = text_tokens(text)

        overlap_recent = len(today_tokens & recent_tokens)
        overlap_older = len(today_tokens & older_tokens)
        total_history_dates = len(global_tokens_by_date)

        if total_history_dates == 0 or (overlap_recent == 0 and overlap_older == 0):
            trend = "new"
            note = "近 7 天未出现类似主题，是新信号。"
        elif overlap_recent >= 3:
            trend = "sustained"
            note = "近期持续出现，属于活跃主题。"
        elif overlap_recent >= 1 and overlap_older >= 1:
            trend = "rising"
            note = "从近期开始升温，值得持续关注。"
        elif overlap_recent == 0 and overlap_older >= 2:
            trend = "fading"
            note = "近期热度下降，可能已过峰值。"
        else:
            trend = "rising"
            note = "有零星历史信号，今日再次出现。"

        signals.append({
            "cluster_id": cluster_id,
            "trend": trend,
            "note": note,
        })
    return signals


def build_llm_briefing_data(
    today_clusters: list[dict[str, Any]],
    signals: list[dict[str, str]],
    alerts: list[dict[str, object]],
    watch_radar: list[dict[str, object]],
) -> list[dict[str, object]]:
    signal_map = {s["cluster_id"]: s for s in signals}
    cluster_data: list[dict[str, object]] = []
    for i, cluster in enumerate(today_clusters):
        cid = cluster.get("cluster_id", "")
        sig = signal_map.get(cid, {})
        cluster_data.append({
            "cluster_id": cid,
            "title": cluster.get("title", ""),
            "category": cluster.get("category", ""),
            "score": round(float(cluster.get("score", 0)), 1),
            "size": int(cluster.get("size", 1)),
            "explanation": (cluster.get("explanation") or "")[:200],
            "trend": sig.get("trend", ""),
            "trend_note": sig.get("note", ""),
        })

    alert_data: list[dict[str, object]] = []
    for alert in alerts[:6]:
        alert_data.append({
            "title": alert.get("title") or alert.get("item_title", ""),
            "detail": str(alert.get("detail", ""))[:150],
            "confidence": alert.get("confidence", 0),
        })

    radar_data: list[dict[str, object]] = []
    for target in watch_radar:
        radar_data.append({
            "name": target.get("name", ""),
            "status": target.get("status", ""),
            "match_count": target.get("match_count", 0),
            "confidence": round(float(target.get("confidence", 0)), 2),
        })

    return [
        {
            "role": "clusters",
            "items": cluster_data,
        },
        {
            "role": "alerts",
            "items": alert_data,
        },
        {
            "role": "watch_radar",
            "items": radar_data,
        },
    ]


def call_llm_briefing(
    settings: Settings,
    payload: list[dict[str, object]],
    db_path: Path,
    report_date: str,
) -> dict[str, object]:
    section = settings.section("llm")
    if not section.get("enabled", True):
        record_llm_job(db_path, report_date, "intel_briefing", "skipped", "", 0, "disabled")
        return {}

    base_url_env = str(section.get("base_url_env", "OPENAI_BASE_URL"))
    fallback_base_url_env = str(section.get("fallback_base_url_env", ""))
    api_key_env = str(section.get("api_key_env", "OPENAI_API_KEY"))
    fallback_api_key_env = str(section.get("fallback_api_key_env", ""))
    base_url = env_value(base_url_env, fallback_base_url_env) or str(section.get("base_url", "https://api.openai.com/v1"))
    api_key = env_value(api_key_env, fallback_api_key_env)
    if not api_key:
        record_llm_job(db_path, report_date, "intel_briefing", "skipped", "", 0, "no api key")
        return {}

    model_list = configured_model_candidates(section, configured_model(section, ""))
    temperature = float(section.get("temperature", 1.0))
    top_p = float(section.get("top_p", 0.95))
    max_tokens = int(section.get("max_tokens", 8000))
    timeout = int(section.get("timeout_seconds", 90))

    used_model = ""
    content = ""
    for model in model_list:
        for budget in token_budgets(max_tokens):
            try:
                resp = request_summary(base_url, api_key, model, SYSTEM_PROMPT, payload, temperature, top_p, budget, timeout)
                content, finish, _ = response_content(resp)
                used_model = model
                if content:
                    break
            except Exception as exc:
                msg = str(exc)
                if "Not supported model" in msg or "empty" in msg.lower():
                    content = ""
                    break
                record_llm_job(db_path, report_date, "intel_briefing", "failed", model, len(payload), msg)
                return {}
        if content:
            break

    if not content:
        record_llm_job(db_path, report_date, "intel_briefing", "fallback", used_model, len(payload), "empty response")
        return {}

    parsed = parse_json_object(content)
    if not parsed or not isinstance(parsed.get("headline"), str):
        record_llm_job(db_path, report_date, "intel_briefing", "fallback", used_model, len(payload), "no headline in response")
        return {}

    record_llm_job(db_path, report_date, "intel_briefing", "ok", used_model, len(payload), "")
    return {"headline": parsed["headline"], "analysis": parsed.get("analysis", ""), "signals": parsed.get("signals", []), "watch_digest": parsed.get("watch_digest", ""), "model": used_model}


def local_fallback_briefing(
    today_clusters: list[dict[str, Any]],
    signals: list[dict[str, str]],
    watch_radar: list[dict[str, object]],
) -> dict[str, object]:
    signal_map = {s["cluster_id"]: s for s in signals}
    titles: list[str] = []
    for cluster in today_clusters[:3]:
        trend = signal_map.get(cluster.get("cluster_id", ""), {}).get("trend", "")
        prefix = TREND_LABELS.get(trend, "")
        title = cluster.get("title", "未命名")
        titles.append(f"{f'[{prefix}] ' if prefix else ''}{title}")

    headline = "、".join(titles) if titles else "今日暂无明显主线信号"
    parts: list[str] = []
    new_count = sum(1 for s in signals if s.get("trend") == "new")
    rising_count = sum(1 for s in signals if s.get("trend") == "rising")
    if new_count:
        parts.append(f"{new_count} 个新信号出现")
    if rising_count:
        parts.append(f"{rising_count} 个主题在升温")
    sustained_count = sum(1 for s in signals if s.get("trend") == "sustained")
    if sustained_count:
        parts.append(f"{sustained_count} 个主题持续活跃")

    analysis = f"今日共 {len(today_clusters)} 条主线。" + "，".join(parts) + "。" if parts else f"今日共 {len(today_clusters)} 条主线，建议按排序逐一查看。"

    radar_parts: list[str] = []
    for target in watch_radar[:4]:
        status = target.get("status", "quiet")
        name = target.get("name", "")
        if status == "active":
            radar_parts.append(f"{name} 活跃")
        elif status == "warning":
            radar_parts.append(f"{name} 有变化")
    watch_digest = "观察雷达：" + "，".join(radar_parts) + "。" if radar_parts else "观察雷达暂无显著变化。"

    return {
        "headline": headline,
        "analysis": analysis,
        "signals": signals,
        "watch_digest": watch_digest,
        "model": "local_rule",
    }


def build_intel_briefing(
    settings: Settings,
    today_clusters: list[dict[str, Any]],
    items: list[Item],
    db_path: Path,
    report_date: str,
) -> dict[str, object]:
    history = load_historical_cluster_keywords(db_path, report_date, days=7)
    signals = compute_trend_signals(today_clusters, history, report_date)
    watch_radar = load_watch_radar(db_path, report_date)

    section = settings.section("analyzer")
    if not section.get("enabled", True):
        result = local_fallback_briefing(today_clusters, signals, watch_radar)
        save_result(db_path, report_date, result)
        return result

    from app.alerts import alert_candidates
    candidate_items = alert_candidates(items)
    alerts: list[dict[str, object]] = []
    for item in candidate_items[:6]:
        alerts.append({
            "title": item.title,
            "detail": item.ai_summary or item.compact_summary(150),
            "confidence": min(1.0, (item.rank_score or 0) / 100),
        })

    payload = build_llm_briefing_data(today_clusters, signals, alerts, watch_radar)
    llm_result = call_llm_briefing(settings, payload, db_path, report_date)

    if llm_result and llm_result.get("headline"):
        llm_result.setdefault("signals", signals)
        llm_result.setdefault("watch_digest", "")
        save_result(db_path, report_date, llm_result, generation="llm")
        return llm_result

    fallback = local_fallback_briefing(today_clusters, signals, watch_radar)
    save_result(db_path, report_date, fallback)
    return fallback


def save_result(
    db_path: Path,
    report_date: str,
    result: dict[str, object],
    generation: str = "local_rule",
) -> None:
    save_intel_briefing(
        db_path,
        report_date,
        headline=str(result.get("headline", "")),
        analysis=str(result.get("analysis", "")),
        signals=result.get("signals", []) if isinstance(result.get("signals"), list) else [],
        watch_digest=str(result.get("watch_digest", "")),
        generation=generation if result.get("model") != "local_rule" else "local_rule",
        model=str(result.get("model", "")),
    )
