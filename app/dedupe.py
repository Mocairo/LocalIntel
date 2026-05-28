from __future__ import annotations

import hashlib
import re

from app.models import Item


_SPACE_RE = re.compile(r"\s+")


def normalized_title(title: str) -> str:
    return _SPACE_RE.sub(" ", title.casefold()).strip()


def item_hash(item: Item) -> str:
    key = item.canonical_url() or normalized_title(item.title)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def dedupe_items(items: list[Item]) -> list[Item]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    result: list[Item] = []
    for item in sorted(items, key=lambda row: row.score, reverse=True):
        url_key = item.canonical_url()
        title_key = normalized_title(item.title)
        if url_key and url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue
        if url_key:
            seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        result.append(item)
    return result
