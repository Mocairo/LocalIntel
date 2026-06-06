from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path
from typing import Any

from app.config import Settings, load_settings
from app.preferences import load_preferences


def read_ui_config(settings: Settings) -> dict[str, object]:
    preferences = load_preferences(settings.app_path("interests_file"))
    interests_data = read_toml(settings.app_path("interests_file"))
    return {
        "app": {
            "daily_time": settings.section("app").get("daily_time", "08:30"),
            "days_back": settings.section("app").get("days_back", 1),
        },
        "github": {
            "enabled": settings.section("github").get("enabled", True),
            "limit": settings.section("github").get("limit", 10),
            "trending_since": settings.section("github").get("trending_since", "daily"),
            "trending_languages": settings.section("github").get("trending_languages", [""]),
        },
        "arxiv": {
            "enabled": settings.section("arxiv").get("enabled", True),
            "categories": settings.section("arxiv").get("categories", []),
            "keywords": settings.section("arxiv").get("keywords", []),
        },
        "gdelt": {
            "enabled": settings.section("gdelt").get("enabled", True),
            "queries": settings.section("gdelt").get("queries", []),
            "world_days_back": settings.section("gdelt").get("world_days_back", 3),
            "theme_queries_per_run": settings.section("gdelt").get("theme_queries_per_run", 3),
            "theme_pool": settings.section("gdelt").get("theme_pool", []),
        },
        "rss": {
            "enabled": settings.section("rss").get("enabled", True),
            "feeds": settings.section("rss").get("feeds", []),
        },
        "llm": {
            "enabled": settings.section("llm").get("enabled", True),
            "model": settings.section("llm").get("model", "mimo-v2.5-pro"),
            "max_items": settings.section("llm").get("max_items", 40),
            "max_tokens": settings.section("llm").get("max_tokens", 8000),
        },
        "translation": {
            "enabled": settings.section("translation").get("enabled", True),
            "provider": settings.section("translation").get("provider", "llm"),
            "model": settings.section("translation").get("model", "mimo-v2.5-pro"),
            "max_items": settings.section("translation").get("max_items", 20),
        },
        "interests": {
            "priority_topics": preferences.priority_topics,
            "blocked_keywords": preferences.blocked_keywords,
            "preferred_domains": preferences.preferred_domains,
            "blocked_domains": preferences.blocked_domains,
            "weights": preferences.weights,
            "watchlist": normalize_watchlist(interests_data.get("watchlist")),
        },
    }


def update_config(config_path: Path, env_path: Path, payload: dict[str, Any]) -> Settings:
    settings = load_settings(config_path, env_path)
    values = settings.values
    for section_name in ("app", "github", "arxiv", "gdelt", "rss", "llm", "translation"):
        incoming = payload.get(section_name)
        if not isinstance(incoming, dict):
            continue
        section = values.setdefault(section_name, {})
        if not isinstance(section, dict):
            continue
        for key, value in incoming.items():
            if key in allowed_keys(section_name):
                section[key] = normalize_value(key, value)
    write_toml(config_path, values)
    refreshed = load_settings(config_path, env_path)
    interests = payload.get("interests")
    if isinstance(interests, dict):
        update_interests(refreshed.app_path("interests_file"), interests)
    return load_settings(config_path, env_path)


def allowed_keys(section: str) -> set[str]:
    return {
        "app": {"daily_time", "days_back"},
        "github": {"enabled", "limit", "trending_since", "trending_languages"},
        "arxiv": {"enabled", "categories", "keywords"},
        "gdelt": {"enabled", "queries", "world_days_back", "theme_queries_per_run", "theme_pool"},
        "rss": {"enabled", "feeds"},
        "llm": {"enabled", "model", "max_items", "max_tokens"},
        "translation": {"enabled", "provider", "model", "max_items"},
    }.get(section, set())


def normalize_value(key: str, value: Any) -> Any:
    if key == "max_tokens":
        try:
            return max(1000, int(value))
        except (TypeError, ValueError):
            return 8000
    if key in {"days_back", "world_days_back", "theme_queries_per_run", "limit", "max_items"}:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1
    if key == "trending_since":
        text = str(value)
        return text if text in {"daily", "weekly", "monthly"} else "daily"
    return value


def write_toml(path: Path, values: dict[str, Any]) -> None:
    lines: list[str] = []
    for section_name, section in values.items():
        if isinstance(section, list) and all(isinstance(row, dict) for row in section):
            for row in section:
                lines.append(f"[[{section_name}]]")
                for key, value in row.items():
                    lines.append(f"{key} = {format_toml_value(value)}")
                lines.append("")
            continue
        if not isinstance(section, dict):
            continue
        lines.append(f"[{section_name}]")
        for key, value in section.items():
            lines.append(f"{key} = {format_toml_value(value)}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        if all(isinstance(row, dict) for row in value):
            rows = ["["]
            for row in value:
                pairs = ", ".join(f"{key} = {format_toml_value(val)}" for key, val in row.items())
                rows.append(f"  {{ {pairs} }},")
            rows.append("]")
            return "\n".join(rows)
        return "[" + ", ".join(format_toml_value(row) for row in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_interests(path: Path, payload: dict[str, Any]) -> None:
    preferences = load_preferences(path)
    existing = read_toml(path)
    weights = dict(preferences.weights)
    incoming_weights = payload.get("weights", {})
    if isinstance(incoming_weights, dict):
        for key, value in incoming_weights.items():
            try:
                weights[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    values = {
        "interests": {
            "priority_topics": list_value(payload.get("priority_topics"), preferences.priority_topics),
            "blocked_keywords": list_value(payload.get("blocked_keywords"), preferences.blocked_keywords),
            "preferred_languages": preferences.preferred_languages,
        },
        "domains": {
            "blocked": list_value(payload.get("blocked_domains"), preferences.blocked_domains),
            "preferred": list_value(payload.get("preferred_domains"), preferences.preferred_domains),
        },
        "weights": weights,
    }
    if "watchlist" in payload:
        values["watchlist"] = normalize_watchlist(payload.get("watchlist"))
    elif isinstance(existing.get("watchlist"), list):
        values["watchlist"] = existing["watchlist"]
    write_toml(path, values)


def read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError:
        return {}


def list_value(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(row).strip() for row in value if str(row).strip()]
    if isinstance(value, str):
        rows = [row.strip() for row in value.replace(",", "\n").splitlines()]
        return [row for row in rows if row]
    return fallback


def normalize_watchlist(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        keywords = list_value(raw.get("keywords"), [])
        if not name or not keywords:
            continue
        row_id = slugify_watch_id(str(raw.get("id") or name), used_ids, name)
        row_type = str(raw.get("type") or "topic").strip() or "topic"
        rows.append(
            {
                "id": row_id,
                "name": name,
                "type": row_type,
                "enabled": bool(raw.get("enabled", True)),
                "keywords": keywords,
                "description": str(raw.get("description") or "").strip(),
            }
        )
    return rows


def slugify_watch_id(value: str, used_ids: set[str], fallback: str = "") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        digest_source = fallback or value or "watch"
        slug = "watch-" + hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:8]
    candidate = slug
    index = 2
    while candidate in used_ids:
        candidate = f"{slug}-{index}"
        index += 1
    used_ids.add(candidate)
    return candidate
