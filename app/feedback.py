from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from app.dedupe import item_hash
from app.models import Item
from app.ranker import domain_of


def apply_feedback_scores(db_path: Path, items: list[Item]) -> list[Item]:
    profile = load_feedback_profile(db_path)
    if not profile:
        return items
    for item in items:
        before = item.rank_score
        current_hash = item_hash(item)
        domain = domain_of(item.url)
        text = f"{item.title} {item.summary} {item.ai_summary}".casefold()
        if current_hash in profile["favorite_hashes"]:
            item.rank_score += 12
        if current_hash in profile["later_hashes"]:
            item.rank_score += 8
        if current_hash in profile["read_hashes"]:
            item.rank_score -= 4
        if current_hash in profile["archived_hashes"]:
            item.rank_score -= 24
        if current_hash in profile["ignored_hashes"]:
            item.rank_score -= 35
        if domain in profile["liked_domains"]:
            item.rank_score += min(8, 2.5 * profile["liked_domains"][domain])
        if domain in profile["ignored_domains"]:
            item.rank_score -= min(14, 3 * profile["ignored_domains"][domain])
        topic_hits = sum(weight for topic, weight in profile["liked_topics"].items() if topic in text)
        if topic_hits:
            item.rank_score += min(10, topic_hits * 1.8)
        ignored_hits = sum(weight for topic, weight in profile["ignored_topics"].items() if topic in text)
        if ignored_hits:
            item.rank_score -= min(16, ignored_hits * 2.2)
        item.rank_score = round(max(0, item.rank_score), 2)
        if item.rank_score > before + 2 and "根据你的偏好上调" not in item.top_reason:
            item.top_reason = append_reason(item.top_reason, "根据你的收藏和打开记录上调。")
        if item.rank_score < before - 2 and "根据你的忽略记录下调" not in item.top_reason:
            item.top_reason = append_reason(item.top_reason, "根据你的忽略记录下调。")
    return sorted(items, key=lambda row: row.rank_score, reverse=True)


def load_feedback_profile(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        return {}
    from app.db import init_db

    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT i.hash, i.title, i.url, i.tags_json, m.favorite, m.ignored,
                   COALESCE(m.read_status, 'unread') AS read_status,
                   COALESCE(opened.opens, 0) AS opens
            FROM items i
            LEFT JOIN user_marks m ON m.item_hash = i.hash
            LEFT JOIN (
                SELECT item_hash, SUM(value) AS opens
                FROM user_events
                WHERE event_type IN ('open', 'detail')
                GROUP BY item_hash
            ) opened ON opened.item_hash = i.hash
            WHERE COALESCE(m.favorite, 0) = 1
               OR COALESCE(m.ignored, 0) = 1
               OR COALESCE(m.read_status, 'unread') IN ('later', 'archived', 'read')
               OR COALESCE(opened.opens, 0) > 0
            """
        ).fetchall()
    favorite_hashes: set[str] = set()
    ignored_hashes: set[str] = set()
    later_hashes: set[str] = set()
    archived_hashes: set[str] = set()
    read_hashes: set[str] = set()
    liked_domains: Counter[str] = Counter()
    ignored_domains: Counter[str] = Counter()
    liked_topics: Counter[str] = Counter()
    ignored_topics: Counter[str] = Counter()
    for row in rows:
        domain = domain_of(row["url"])
        tags = parse_tags(row["tags_json"])
        terms = tags or title_terms(row["title"])
        opens = float(row["opens"] or 0)
        if row["favorite"] or opens:
            if row["favorite"]:
                favorite_hashes.add(row["hash"])
            liked_domains[domain] += 2 if row["favorite"] else min(2, opens)
            for term in terms:
                liked_topics[term] += 2 if row["favorite"] else min(2, opens)
        if row["ignored"]:
            ignored_hashes.add(row["hash"])
            ignored_domains[domain] += 2
            for term in terms:
                ignored_topics[term] += 2
        if row["read_status"] == "later":
            later_hashes.add(row["hash"])
        if row["read_status"] == "archived":
            archived_hashes.add(row["hash"])
        if row["read_status"] == "read":
            read_hashes.add(row["hash"])
    return {
        "favorite_hashes": favorite_hashes,
        "ignored_hashes": ignored_hashes,
        "later_hashes": later_hashes,
        "archived_hashes": archived_hashes,
        "read_hashes": read_hashes,
        "liked_domains": liked_domains,
        "ignored_domains": ignored_domains,
        "liked_topics": liked_topics,
        "ignored_topics": ignored_topics,
    }


def parse_tags(value: str) -> list[str]:
    try:
        tags = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(tags, list):
        return []
    return [str(tag).casefold() for tag in tags if str(tag).strip()]


def title_terms(title: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", title.casefold())
        if token not in {"with", "from", "that", "this", "into", "over"}
    ][:5]


def append_reason(current: str, extra: str) -> str:
    if not current:
        return extra
    return f"{current} {extra}"
