from __future__ import annotations

from app.models import Item


BUCKET_LABELS = {
    "must": "必看",
    "scan": "可扫",
    "archive": "归档",
}


def assign_buckets(items: list[Item], must_limit: int = 5) -> list[Item]:
    ranked = sorted(items, key=lambda item: item.rank_score, reverse=True)
    must_hashes = {id(item) for item in ranked[:must_limit]}
    for item in ranked:
        if id(item) in must_hashes:
            item.bucket = "must"
        elif item.rank_score >= 72 or item.importance >= 4:
            item.bucket = "scan"
        else:
            item.bucket = "archive"
    return ranked
