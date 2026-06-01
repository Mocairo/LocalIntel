from __future__ import annotations

import json
import re

from app.config import Settings
from app.http import fetch_json, post_json
from app.models import Item
from app.summarizer import chat_url, env_value


def translate_world_news(settings: Settings, items: list[Item]) -> str:
    section = settings.section("translation")
    if not section.get("enabled", True):
        return ""
    targets = [item for item in items if item.category == "world_news"][: int(section.get("max_items", 20))]
    if not targets:
        return ""
    provider = str(section.get("provider", "public"))
    if provider == "public":
        timeout = public_translation_timeout(section)
        for item in targets:
            value = translate_with_public_api(item.title, item.summary, timeout=timeout)
            if value:
                item.ai_summary = value
                item.why = "已翻译为中文，便于快速浏览全球时事。"
            elif not item.ai_summary:
                item.ai_summary = local_translate_stub(item)
        return ""

    api_key_env = str(settings.section("llm").get("api_key_env", "MIMO_API_KEY"))
    fallback_api_key_env = str(settings.section("llm").get("fallback_api_key_env", "OPENAI_API_KEY"))
    api_key = env_value(api_key_env, fallback_api_key_env)
    base_url_env = str(settings.section("llm").get("base_url_env", "MiMO_BASE_URL"))
    fallback_base_url_env = str(settings.section("llm").get("fallback_base_url_env", "OPENAI_BASE_URL"))
    base_url = env_value(base_url_env, fallback_base_url_env)
    if not api_key or not base_url:
        return "Translation skipped: LLM API key/base_url is not set."

    model = str(section.get("model") or settings.section("llm").get("model") or "mimo-v2.5")
    batch_size = max(1, int(section.get("batch_size", 3)))
    timeout = int(section.get("timeout_seconds", 45))
    errors: list[str] = []
    for start in range(0, len(targets), batch_size):
        batch = targets[start : start + batch_size]
        try:
            translated = translate_batch(base_url, api_key, model, batch, timeout)
        except Exception as exc:
            errors.append(str(exc))
            translated = {}
        for item in batch:
            value = translated.get(item.source_id) or translated.get(item.url)
            if not value:
                value = translate_with_public_api(item.title, item.summary, timeout=timeout)
            if value:
                item.ai_summary = value
                item.why = "已翻译为中文，便于快速浏览全球时事。"
            elif not item.ai_summary:
                item.ai_summary = local_translate_stub(item)
    return "; ".join(errors[:3])


def public_translation_timeout(section: dict[str, object]) -> int:
    configured = int(section.get("public_timeout_seconds", section.get("timeout_seconds", 6)))
    return max(1, min(configured, 6))


def translate_batch(base_url: str, api_key: str, model: str, batch: list[Item], timeout: int) -> dict[str, str]:
    lines = []
    for index, item in enumerate(batch, 1):
        lines.append(
            f"{index}. id={item.source_id}\n"
            f"标题：{item.title}\n"
            f"信息：{item.summary}\n"
        )
    prompt = (
        "请把下面全球新闻条目翻译成中文。只输出 JSON 对象，不要 Markdown，不要解释。\n"
        "JSON 格式：{\"id\":\"中文标题。中文一句话摘要\"}。\n"
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
            "max_tokens": 1000,
        },
        timeout=timeout,
        headers={"Authorization": f"Bearer {api_key}"},
        retries=1,
    )
    if not isinstance(response, dict):
        return {}
    content = str(response.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
    parsed = parse_translation_json(content)
    if parsed:
        return parsed
    return parse_translation_lines(content, batch)


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
    return {str(key): str(value).strip() for key, value in data.items() if str(value).strip()}


def parse_translation_lines(content: str, batch: list[Item]) -> dict[str, str]:
    result: dict[str, str] = {}
    rows = [line.strip() for line in content.splitlines() if line.strip()]
    for item, row in zip(batch, rows):
        result[item.source_id] = re.sub(r"^\d+[.)、]\s*", "", row)
    return result


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
