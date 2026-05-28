from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from app.models import Item
from app.preferences import Preferences


SOURCE_QUALITY = {
    "hackernews": 0.78,
    "github": 0.72,
    "github_trending": 0.86,
    "github_release": 0.82,
    "arxiv": 0.86,
    "gdelt": 0.52,
}

MOJIBAKE_MARKERS = ("�", "脟", "艧", "謀", "臒", "膮", "賱", "褌", "泻", "械", "眉", "铆", "茅", "贸")


def rank_items(items: list[Item], preferences: Preferences, run_date: date) -> list[Item]:
    filtered: list[Item] = []
    for item in items:
        if should_drop(item, preferences):
            continue
        item.rank_score = compute_rank(item, preferences, run_date)
        if not item.importance:
            item.importance = max(1, min(5, round(item.rank_score / 20)))
        if not item.tags:
            item.tags = infer_tags(item, preferences)
        item.top_reason = build_top_reason(item)
        filtered.append(item)
    return sorted(filtered, key=lambda row: row.rank_score, reverse=True)


def should_drop(item: Item, preferences: Preferences) -> bool:
    text = f"{item.title} {item.summary} {item.content}".casefold()
    if any(keyword.casefold() in text for keyword in preferences.blocked_keywords):
        return True
    domain = domain_of(item.url)
    if any(domain.endswith(blocked.casefold()) for blocked in preferences.blocked_domains):
        return True
    if item.source == "gdelt" and looks_corrupt(item.title):
        return True
    if item.source == "gdelt" and not language_allowed(str(item.raw.get("language") or ""), preferences):
        return True
    return False


def select_highlights(items: list[Item], limit: int = 5) -> list[Item]:
    selected: list[Item] = []
    seen_keys: set[str] = set()
    for item in sorted(items, key=lambda row: row.rank_score, reverse=True):
        key = diversity_key(item)
        if key in seen_keys and len(selected) < limit - 1:
            continue
        selected.append(item)
        seen_keys.add(key)
        if len(selected) >= limit:
            return selected
    return selected


def diversity_key(item: Item) -> str:
    if item.source == "github_release":
        return f"github_release:{item.raw.get('repo', '')}"
    if item.source == "github_trending":
        return f"github_trending:{item.source_id}"
    if item.source == "github":
        return f"github:{str(item.source_id).split('/')[0]}"
    domain = domain_of(item.url)
    return f"{item.source}:{domain or item.category}"


def compute_rank(item: Item, preferences: Preferences, run_date: date) -> float:
    weights = preferences.weights
    freshness = freshness_score(item, run_date)
    quality = source_quality(item, preferences)
    interest = interest_score(item, preferences)
    popularity = popularity_score(item)
    source_bonus = 1.0 if item.source in ("hackernews", "github_trending", "github_release", "arxiv") else 0.55
    value = (
        weights.get("freshness", 0.35) * freshness
        + weights.get("source_quality", 0.2) * quality
        + weights.get("personal_interest", 0.25) * interest
        + weights.get("popularity", 0.15) * popularity
        + weights.get("source_bonus", 0.05) * source_bonus
    )
    return round(value * 100, 2)


def freshness_score(item: Item, run_date: date) -> float:
    dt = item_datetime(item)
    if item.source == "github":
        created = parse_datetime(str(item.raw.get("created_at", "")))
        pushed = parse_datetime(str(item.raw.get("pushed_at", "")))
        if created:
            created_days = max(0, (run_date - created.date()).days)
            if created_days <= 1:
                return 1.0
            if created_days <= 7:
                return 0.74
        if pushed:
            pushed_days = max(0, (run_date - pushed.date()).days)
            if pushed_days <= 1:
                return 0.48
        return 0.18
    if item.source == "github_trending":
        return 1.0
    if not dt:
        return 0.35
    days = max(0, (run_date - dt.date()).days)
    if days == 0:
        return 1.0
    if days == 1:
        return 0.86
    if days <= 3:
        return 0.64
    if days <= 7:
        return 0.42
    if days <= 30:
        return 0.18
    return 0.05


def source_quality(item: Item, preferences: Preferences) -> float:
    base = SOURCE_QUALITY.get(item.source, 0.62)
    if item.source.startswith("rss:"):
        base = 0.7
    domain = domain_of(item.url)
    if any(domain.endswith(preferred.casefold()) for preferred in preferences.preferred_domains):
        base += 0.18
    return min(1.0, base)


def interest_score(item: Item, preferences: Preferences) -> float:
    if not preferences.priority_topics:
        return 0.5
    text = f"{item.title} {item.summary} {item.content}"
    text_folded = text.casefold()
    hits = sum(1 for topic in preferences.priority_topics if topic_matches(topic, text_folded))
    return min(1.0, 0.25 + hits * 0.22)


def popularity_score(item: Item) -> float:
    if item.source == "github":
        stars = float(item.raw.get("stargazers_count") or item.score or 0)
        return min(1.0, math.log10(stars + 10) / 5)
    if item.source == "github_trending":
        stars_today = float(item.raw.get("stars_today") or 0)
        total_stars = float(item.raw.get("total_stars") or 0)
        today_score = min(1.0, math.log10(stars_today + 10) / 3.2)
        total_score = min(1.0, math.log10(total_stars + 10) / 5)
        return today_score * 0.72 + total_score * 0.28
    if item.source == "hackernews":
        comments = float(item.raw.get("descendants") or 0)
        points = float(item.score or 0)
        return min(1.0, (math.log10(points + 10) + math.log10(comments + 10)) / 6)
    if item.source == "github_release":
        return 0.72
    return min(1.0, max(0.2, math.log10(float(item.score or 1) + 10) / 4))


def infer_tags(item: Item, preferences: Preferences) -> list[str]:
    text = f"{item.title} {item.summary}".casefold()
    tags = [topic for topic in preferences.priority_topics if topic_matches(topic, text)][:4]
    if item.category not in ("general", ""):
        tags.insert(0, item.category)
    world_theme = str(item.raw.get("world_theme") or "").strip()
    if item.category == "world_news" and world_theme:
        tags.insert(1, world_theme)
    result: list[str] = []
    for tag in tags:
        if tag not in result:
            result.append(tag)
    return result[:5]


def topic_matches(topic: str, text: str) -> bool:
    value = topic.casefold().strip()
    if not value:
        return False
    if re.fullmatch(r"[a-z0-9+#.]{1,3}", value):
        return re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", text) is not None
    return value in text


def build_top_reason(item: Item) -> str:
    if item.source == "github":
        created = str(item.raw.get("created_at", ""))[:10]
        pushed = str(item.raw.get("pushed_at", ""))[:10]
        stars = item.raw.get("stargazers_count") or 0
        return f"GitHub 项目，创建于 {created or '未知'}，最近更新 {pushed or '未知'}，当前 {stars} stars。"
    if item.source == "github_trending":
        stars_today = float(item.raw.get("stars_today") or 0)
        total_stars = float(item.raw.get("total_stars") or 0)
        since = item.raw.get("trending_since") or "daily"
        return f"GitHub Trending（{since}），新增约 {stars_today:g} stars，总计 {total_stars:g} stars。"
    if item.source == "github_release":
        return "重点开源项目发布了新版本，适合检查升级内容。"
    if item.source == "hackernews":
        return item.summary
    if item.source == "arxiv":
        return "近期论文，适合加入技术跟踪清单。"
    return item.compact_summary(120)


def item_datetime(item: Item) -> datetime | None:
    for value in (
        item.published_at,
        str(item.raw.get("published_at", "")),
        str(item.raw.get("created_at", "")),
        str(item.raw.get("pushed_at", "")),
    ):
        parsed = parse_datetime(value)
        if parsed:
            return parsed
    return None


def parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.casefold().removeprefix("www.")


def looks_corrupt(text: str) -> bool:
    if not text:
        return True
    marker_hits = sum(text.count(marker) for marker in MOJIBAKE_MARKERS)
    if marker_hits >= 2:
        return True
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    total_letters = len(re.findall(r"\w", text, flags=re.UNICODE))
    if total_letters >= 12 and ascii_letters / max(1, total_letters) < 0.15 and any(ord(ch) > 255 for ch in text):
        return True
    return False


def language_allowed(language: str, preferences: Preferences) -> bool:
    if not language:
        return True
    normalized = language.casefold()
    allowed = {lang.casefold() for lang in preferences.preferred_languages}
    if not allowed:
        return True
    aliases = {
        "en": {"en", "eng", "english"},
        "zh": {"zh", "zho", "chi", "chinese", "mandarin"},
    }
    for allowed_lang in allowed:
        names = aliases.get(allowed_lang, {allowed_lang})
        if normalized in names:
            return True
    return normalized in allowed
