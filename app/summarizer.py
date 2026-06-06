from __future__ import annotations

import json
import os
import re

from app.config import Settings
from app.dedupe import item_hash
from app.db import record_llm_job
from app.http import post_json
from app.models import Item


DEFAULT_MIMO_MODEL = "mimo-v2.5-pro"
MIMO_MODEL_ORDER = ("mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-pro")


def build_llm_summary(settings: Settings, items: list[Item], report_date: str = "") -> str:
    section = settings.section("llm")
    if not section.get("enabled", False):
        return ""

    db_path = settings.app_path("data_dir") / "intel.sqlite"
    job_date = report_date or "unknown"
    model = str(section.get("model", DEFAULT_MIMO_MODEL))
    api_key_env = str(section.get("api_key_env", "MIMO_API_KEY"))
    fallback_api_key_env = str(section.get("fallback_api_key_env", "OPENAI_API_KEY"))
    api_key = env_value(api_key_env, fallback_api_key_env)
    if not api_key:
        record_llm_job(db_path, job_date, "daily_summary", "skipped", model, len(items), f"{api_key_env} is not set")
        return local_daily_summary(items, f"LLM skipped: {api_key_env} is not set.")

    base_url_env = str(section.get("base_url_env", "MiMO_BASE_URL"))
    fallback_base_url_env = str(section.get("fallback_base_url_env", "OPENAI_BASE_URL"))
    base_url = env_value(base_url_env, fallback_base_url_env) or "https://api.openai.com/v1"
    model_candidates = configured_model_candidates(section, model)
    max_items = int(section.get("max_items", 40))
    max_tokens = int(section.get("max_tokens", 8000))
    timeout_seconds = int(section.get("timeout_seconds", 90))
    temperature = float(section.get("temperature", 1.0))
    top_p = float(section.get("top_p", 0.95))
    selected = sorted(items, key=lambda item: item.rank_score, reverse=True)[:max_items]
    payload_items = [
        {
            "hash": item_hash(item),
            "source": item.source,
            "category": item.category,
            "title": item.title,
            "url": item.url,
            "published_at": item.published_at,
            "summary": item.compact_summary(220),
            "rank_score": item.rank_score,
            "tags": item.tags,
        }
        for item in selected
    ]

    prompt = (
        "只根据输入条目生成中文日报摘要，不要编造。"
        "直接输出 JSON，不要 Markdown。"
        '格式：{"overview":"80字内中文总览","highlights":["hash"],'
        '"items":[{"hash":"hash","zh_summary":"一句话中文摘要","why":"重要原因",'
        '"risk":"风险提示","action":"建议动作","importance":1,"tags":["标签"]}]}。'
        "importance 为 1-5，highlights 最多 5 个。"
    )
    response: object = {}
    last_error = ""
    used_model = ""
    for candidate in model_candidates:
        try:
            for token_budget in token_budgets(max_tokens):
                response = request_summary(
                    base_url,
                    api_key,
                    candidate,
                    prompt,
                    payload_items,
                    temperature,
                    top_p,
                    token_budget,
                    timeout_seconds,
                )
                content, finish_reason, reasoning_length = response_content(response)
                if content:
                    used_model = candidate
                    break
                last_error = (
                    f"LLM returned empty content for {candidate}; "
                    f"finish_reason={finish_reason or 'unknown'}; "
                    f"reasoning_content_len={reasoning_length}; max_tokens={token_budget}"
                )
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
        record_llm_job(db_path, job_date, "daily_summary", "failed", model, len(selected), last_error)
        return local_daily_summary(items, f"LLM failed: {last_error}")
    if not isinstance(response, dict):
        record_llm_job(db_path, job_date, "daily_summary", "failed", used_model, len(selected), "invalid response")
        return local_daily_summary(items, "LLM failed: invalid response.")
    content, _, _ = response_content(response)
    if not content:
        error = json.dumps(response, ensure_ascii=False)[:500]
        record_llm_job(db_path, job_date, "daily_summary", "failed", used_model, len(selected), error)
        return local_daily_summary(items, f"LLM failed: {json.dumps(response, ensure_ascii=False)[:500]}")

    parsed = parse_json_object(content)
    if not parsed:
        summary = fallback_model_summary(content, items, used_model)
        if is_useful_model_summary(summary):
            record_llm_job(db_path, job_date, "daily_summary", "ok", used_model, len(selected), "plain text response")
            return summary
        record_llm_job(db_path, job_date, "daily_summary", "fallback", used_model, len(selected), "non-json content")
        return summary
    enrich_items(items, parsed)
    overview = str(parsed.get("overview") or "").strip()
    highlights = parsed.get("highlights", [])
    if isinstance(highlights, list):
        highlight_hashes = {str(row) for row in highlights[:5]}
        for item in items:
            if item_hash(item) in highlight_hashes:
                item.rank_score += 8
    prefix = f"LLM 模型：{used_model}\n\n"
    record_llm_job(db_path, job_date, "daily_summary", "ok", used_model, len(selected), "")
    return prefix + (overview or local_daily_summary(items, ""))


def configured_model_candidates(section: dict[str, object], model: str) -> list[str]:
    raw_candidates = [str(row).strip() for row in section.get("model_candidates", []) if str(row).strip()]
    raw_candidates.insert(0, str(model).strip() or DEFAULT_MIMO_MODEL)
    uses_mimo = any(candidate.startswith("mimo-") for candidate in raw_candidates)
    preferred = list(MIMO_MODEL_ORDER) if uses_mimo else []
    candidates: list[str] = []
    for candidate in preferred + raw_candidates:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates or [DEFAULT_MIMO_MODEL]


def token_budgets(max_tokens: int) -> list[int]:
    first = max(1000, max_tokens)
    second = max(first * 2, 4000)
    if second == first:
        return [first]
    return [first, min(second, 12000)]


def request_summary(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    payload_items: list[dict[str, object]],
    temperature: float,
    top_p: float,
    max_tokens: int,
    timeout_seconds: int,
) -> object:
    return post_json(
        chat_url(base_url),
        {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload_items, ensure_ascii=False)},
            ],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        },
        timeout=timeout_seconds,
        headers={"Authorization": f"Bearer {api_key}"},
        retries=1,
    )


def response_content(response: object) -> tuple[str, str, int]:
    if not isinstance(response, dict):
        return "", "", 0
    try:
        choice = response["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        return "", "", 0
    if not isinstance(choice, dict) or not isinstance(message, dict):
        return "", "", 0
    content = str(message.get("content") or "").strip()
    finish_reason = str(choice.get("finish_reason") or "")
    reasoning = str(message.get("reasoning_content") or "")
    return content, finish_reason, len(reasoning)


def env_value(primary: str, fallback: str = "") -> str:
    for name in (primary, fallback):
        if not name:
            continue
        value = os.environ.get(name, "").strip()
        if value:
            return value
        alt = name.upper()
        if alt != name:
            value = os.environ.get(alt, "").strip()
            if value:
                return value
    return ""


def chat_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def parse_json_object(content: str) -> dict[str, object]:
    text = strip_json_fence(str(content or "").strip())
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def strip_json_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return text


def fallback_model_summary(content: str, items: list[Item], used_model: str) -> str:
    overview = extract_json_string_field(content, "overview")
    if overview:
        return model_summary(used_model, overview)
    text = compact_plain_summary(content)
    if text and not text.lstrip().startswith(("{", "[")):
        return model_summary(used_model, text)
    return local_daily_summary(items, f"LLM failed: {used_model} returned malformed JSON.")


def is_useful_model_summary(summary: str) -> bool:
    text = str(summary or "").strip()
    return bool(text.startswith("LLM 模型") and "returned malformed JSON" not in text and "LLM failed" not in text)


def extract_json_string_field(content: str, field: str) -> str:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"', content, re.S)
    if not match:
        return ""
    encoded = match.group(1)
    try:
        value = json.loads(f'"{encoded}"')
    except json.JSONDecodeError:
        value = encoded
    return " ".join(str(value).split())


def compact_plain_summary(content: str, limit: int = 360) -> str:
    text = " ".join(str(content or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def model_summary(model: str, summary: str) -> str:
    text = summary.strip()
    return f"LLM 模型：{model}\n\n{text}" if model else text


def enrich_items(items: list[Item], parsed: dict[str, object]) -> None:
    by_hash = {item_hash(item): item for item in items}
    rows = parsed.get("items", [])
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = by_hash.get(str(row.get("hash") or ""))
        if not item:
            continue
        item.ai_summary = str(row.get("zh_summary") or row.get("summary") or "").strip()
        item.why = str(row.get("why") or "").strip()
        risk = str(row.get("risk") or "").strip()
        action = str(row.get("action") or "").strip()
        if risk:
            item.raw["llm_risk"] = risk[:300]
        if action:
            item.raw["llm_action"] = action[:80]
        item.raw["llm_generation"] = "daily_summary"
        try:
            item.importance = max(1, min(5, int(row.get("importance") or item.importance or 1)))
        except (TypeError, ValueError):
            pass
        tags = row.get("tags", [])
        if isinstance(tags, list):
            item.tags = [str(tag).strip() for tag in tags if str(tag).strip()][:5]
        item.rank_score = round(item.rank_score + item.importance * 1.5, 2)


def local_daily_summary(items: list[Item], note: str) -> str:
    top = sorted(items, key=lambda item: item.rank_score, reverse=True)[:5]
    lines = []
    if note:
        lines.append(note)
        lines.append("")
    lines.append("本地规则已按新鲜度、来源质量、兴趣匹配和热度生成今日排序。")
    for item in top:
        lines.append(f"- {item.title}: {item.top_reason or item.compact_summary(120)}")
    return "\n".join(lines)
