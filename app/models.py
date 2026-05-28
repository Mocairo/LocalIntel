from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit


@dataclass(slots=True)
class Item:
    source: str
    source_id: str
    title: str
    url: str
    published_at: str = ""
    summary: str = ""
    content: str = ""
    category: str = "general"
    score: float = 0.0
    rank_score: float = 0.0
    ai_summary: str = ""
    why: str = ""
    importance: int = 0
    bucket: str = "scan"
    tags: list[str] = field(default_factory=list)
    top_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def canonical_url(self) -> str:
        if not self.url:
            return ""
        parsed = urlsplit(self.url.strip())
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))

    def compact_summary(self, limit: int = 260) -> str:
        text = " ".join((self.summary or self.content or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."
