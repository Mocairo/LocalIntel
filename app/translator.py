from __future__ import annotations

import json
import re
import sqlite3
import time

from app.config import Settings
from app.dedupe import item_hash
from app.db import record_llm_job
from app.http import fetch_json, post_json
from app.models import Item
from app.summarizer import DEFAULT_LLM_MODEL, chat_url, configured_model, env_value


SUCCESS_TRANSLATION_STATUSES = {"llm", "public", "public_fallback"}


def translate_world_news(settings: Settings, items: list[Item], report_date: str = "") -> str:
    section = settings.section("translation")
    if not section.get("enabled", True):
        return ""
    targets = [item for item in items if item.category == "world_news"][: int(section.get("max_items", 20))]
    if not targets:
        return ""
    db_path = settings.app_path("data_dir") / "intel.sqlite"
    provider = str(section.get("provider", "llm")).lower()
    if provider == "public":
        budget = public_translation_budget(section)
        for item in targets:
            if apply_cached_translation(db_path, item):
                continue
            value = translate_with_public_budget(item, budget)
            if value:
                apply_translation(item, value, "public")
        return ""

    api_key_env = str(settings.section("llm").get("api_key_env", "OPENAI_API_KEY"))
    fallback_api_key_env = str(settings.section("llm").get("fallback_api_key_env", ""))
    api_key = env_value(api_key_env, fallback_api_key_env)
    base_url_env = str(settings.section("llm").get("base_url_env", "OPENAI_BASE_URL"))
    fallback_base_url_env = str(settings.section("llm").get("fallback_base_url_env", ""))
    base_url = env_value(base_url_env, fallback_base_url_env)
    if not api_key or not base_url:
        record_llm_job(db_path, report_date or "translation", "world_news_translation", "skipped", "", len(targets), "LLM API key/base_url is not set")
        for item in targets:
            if not apply_cached_translation(db_path, item) and not item.ai_summary:
                apply_translation(item, local_translate_stub(item), "local_fallback")
        return "Translation skipped: LLM API key/base_url is not set."

    model_candidates = translation_model_candidates(section, settings.section("llm"))
    batch_size = max(1, int(section.get("batch_size", 3)))
    timeout = int(section.get("timeout_seconds", 45))
    fallback_provider = str(section.get("fallback_provider", "")).lower()
    errors: list[str] = []
    translated_count = 0
    fallback_count = 0
    remaining = [item for item in targets if not apply_cached_translation(db_path, item)]
    public_budget = public_translation_budget(section)
    used_model = ""
    for start in range(0, len(remaining), batch_size):
        batch = remaining[start : start + batch_size]
        translated: dict[str, str] = {}
        for candidate_model in model_candidates:
            try:
                translated = translate_batch(base_url, api_key, candidate_model, batch, timeout)
            except Exception as exc:
                errors.append(f"{candidate_model}: {exc}")
                translated = {}
            if translated:
                used_model = candidate_model
                break
        for item in batch:
            value = translated.get(item.source_id) or translated.get(item.url)
            if not value and fallback_provider == "public":
                value = translate_with_public_budget(item, public_budget)
            if value:
                apply_translation(item, value, "llm" if translated.get(item.source_id) or translated.get(item.url) else "public_fallback")
                translated_count += 1
            elif not item.ai_summary:
                apply_translation(item, local_translate_stub(item), "local_fallback")
                fallback_count += 1
    job_status = "failed" if errors and not translated_count else ("fallback" if errors or fallback_count else "ok")
    record_llm_job(
        db_path,
        report_date or "translation",
        "world_news_translation",
        job_status,
        used_model or (model_candidates[0] if model_candidates else DEFAULT_LLM_MODEL),
        len(remaining),
        "; ".join(errors[:3]),
    )
    return "; ".join(errors[:3])


def translation_model_candidates(section: dict[str, object], llm_section: dict[str, object]) -> list[str]:
    raw: list[str] = []
    llm_model = configured_model(llm_section)
    for value in (
        llm_model,
        section.get("model"),
        *list(section.get("model_candidates", []) if isinstance(section.get("model_candidates", []), list) else []),
        *list(llm_section.get("model_candidates", []) if isinstance(llm_section.get("model_candidates", []), list) else []),
    ):
        text = str(value or "").strip()
        if text:
            raw.append(text)
    candidates: list[str] = []
    for candidate in raw or [DEFAULT_LLM_MODEL]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def public_translation_timeout(section: dict[str, object]) -> int:
    configured = int(section.get("public_timeout_seconds", section.get("timeout_seconds", 6)))
    return max(1, min(configured, 6))


def public_translation_budget(section: dict[str, object]) -> dict[str, float]:
    return {
        "started": time.monotonic(),
        "timeout": float(public_translation_timeout(section)),
        "max_failures": float(max(0, int(section.get("public_max_failures", section.get("max_failures", 2))))),
        "max_seconds": float(max(1, int(section.get("public_max_seconds", section.get("max_seconds", 45))))),
        "failures": 0.0,
    }


def translate_with_public_budget(item: Item, budget: dict[str, float]) -> str:
    if budget["failures"] >= budget["max_failures"] or time.monotonic() - budget["started"] > budget["max_seconds"]:
        if not item.ai_summary:
            apply_translation(item, local_translate_stub(item), "local_fallback")
        return ""
    value = translate_with_public_api(item.title, item.summary, timeout=int(budget["timeout"]))
    if value:
        return value
    budget["failures"] += 1
    if not item.ai_summary:
        apply_translation(item, local_translate_stub(item), "local_fallback")
    return ""


def translate_batch(base_url: str, api_key: str, model: str, batch: list[Item], timeout: int) -> dict[str, str]:
    lines = []
    key_map: dict[str, str] = {}
    for index, item in enumerate(batch, 1):
        key = f"item_{index}"
        key_map[key] = item.source_id
        lines.append(
            f"{key}\n"
            f"标题：{item.title[:180]}\n"
            f"信息：{item.summary[:360]}\n"
        )
    prompt = (
        "请把下面全球新闻条目翻译成中文。只输出 JSON 对象，不要 Markdown，不要解释。\n"
        "必须使用输入中的短编号作为 JSON key，例如：{\"item_1\":\"中文标题。中文一句话摘要\"}。\n"
        "不要输出 id 字段，不要输出数组，不要保留英文标题。\n"
        "如果原文已经是中文，也请润色为简洁中文。\n\n"
        + "\n".join(lines)
    )
    response = post_json(
        chat_url(base_url),
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": max(2400, 900 * len(batch)),
        },
        timeout=timeout,
        headers={"Authorization": f"Bearer {api_key}"},
        retries=2,
    )
    if not isinstance(response, dict):
        return {}
    content = str(response.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
    parsed = parse_translation_json(content)
    if not parsed:
        parsed = parse_translation_lines(content, batch)
    result: dict[str, str] = {}
    for key, value in parsed.items():
        text = clean_translation_text(value)
        if text:
            result[key_map.get(key, key)] = text
    return result


def parse_translation_json(content: str) -> dict[str, str]:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in data.items():
        text = clean_translation_text(value)
        if text:
            result[str(key)] = text
    return result


def parse_translation_lines(content: str, batch: list[Item]) -> dict[str, str]:
    result: dict[str, str] = {}
    rows = [line.strip() for line in content.splitlines() if line.strip()]
    for item, row in zip(batch, rows):
        text = extract_translation_fragment(row)
        if text:
            result[item.source_id] = text
    return result


def clean_translation_text(value: object) -> str:
    if isinstance(value, dict):
        for key in ("translation", "translated", "text", "summary", "title", "zh"):
            if value.get(key):
                return clean_translation_text(value[key])
        value = "。".join(str(item) for item in value.values() if str(item).strip())
    if isinstance(value, list):
        value = "。".join(str(item) for item in value if str(item).strip())
    return extract_translation_fragment(str(value or ""))


def extract_translation_fragment(text: str) -> str:
    row = " ".join(str(text or "").split()).strip()
    if not row:
        return ""
    quoted = re.findall(r'"([^"]*[\u4e00-\u9fff][^"]*)"', row)
    if quoted:
        row = max(quoted, key=len).strip()
    elif "{" in row or re.search(r'"\s*:', row):
        fragments = re.findall(r'[:：,，]\s*"?([^"{}]*[\u4e00-\u9fff][^"{}]*)', row)
        if fragments:
            row = max(fragments, key=len).strip()
    row = re.sub(r"^(item_\d+|\d+)[.:：、)\]\s-]+", "", row).strip()
    row = row.strip("`'\"{}[] ，,")
    return row if any("\u4e00" <= ch <= "\u9fff" for ch in row) else ""


def apply_cached_translation(db_path, item: Item) -> bool:
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT ai_summary, raw_json FROM items WHERE hash = ?",
                (item_hash(item),),
            ).fetchone()
    except sqlite3.Error:
        return False
    if not row:
        return False
    ai_summary = clean_translation_text(row[0])
    try:
        raw = json.loads(str(row[1] or "{}"))
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    status = str(raw.get("translation_status") or "").strip()
    if not ai_summary or not status:
        return False
    if status not in SUCCESS_TRANSLATION_STATUSES:
        return False
    item.ai_summary = ai_summary
    for key in ("original_title", "zh_title", "translation_status", "translation_provider"):
        if raw.get(key):
            item.raw[key] = clean_translation_text(raw[key]) if key == "zh_title" else raw[key]
    if not item.raw.get("zh_title"):
        zh_title = extract_zh_title(ai_summary)
        if zh_title:
            item.raw["zh_title"] = zh_title
    if not item.why:
        item.why = translation_note(status)
    return True


def apply_translation(item: Item, translated: str, status: str) -> None:
    text = clean_translation_text(translated) or " ".join(str(translated or "").split())
    if not text:
        return
    item.raw.setdefault("original_title", item.title)
    item.raw["translation_status"] = status
    item.raw["translation_provider"] = status
    zh_title = extract_zh_title(text)
    if zh_title:
        item.raw["zh_title"] = zh_title
    item.ai_summary = text
    if not item.why:
        item.why = translation_note(status)


def extract_zh_title(text: str) -> str:
    if not text:
        return ""
    first = re.split(r"(?<=[。！？!?])\s*", text, maxsplit=1)[0].strip()
    first = first.rstrip("。！？!?").strip()
    if len(first) > 90:
        return ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in first):
        return ""
    return first


def translation_note(status: str) -> str:
    if status in {"local_fallback", "unavailable"}:
        return "翻译不可用，已显示原文和来源摘要。"
    if status == "public_fallback":
        return "LLM 翻译不可用，已使用公共翻译回退并保留原文链接。"
    return "已翻译为中文，原文标题和链接保留。"


def local_translate_stub(item: Item) -> str:
    return f"{item.title}。来源：{item.summary}"


def translate_with_public_api(title: str, summary: str, timeout: int) -> str:
    text = f"{title}. {summary}".strip()
    if not text:
        return ""
    from urllib.parse import urlencode

    params = urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text})
    try:
        data = fetch_json(f"https://translate.googleapis.com/translate_a/single?{params}", timeout=timeout)
    except Exception:
        return ""
    try:
        parts = data[0]
    except (TypeError, IndexError):
        return ""
    translated = "".join(str(row[0]) for row in parts if isinstance(row, list) and row)
    return translated.strip()
