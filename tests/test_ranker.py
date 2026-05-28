from __future__ import annotations

from datetime import date

from app.models import Item
from app.preferences import Preferences
from app.ranker import rank_items, source_quality


def preferences() -> Preferences:
    return Preferences(
        priority_topics=["AI", "Python"],
        blocked_keywords=["lottery", "彩票"],
        preferred_languages=["zh", "en"],
        blocked_domains=[],
        preferred_domains=["openai.com"],
        weights={
            "freshness": 0.35,
            "source_quality": 0.2,
            "personal_interest": 0.25,
            "popularity": 0.15,
            "source_bonus": 0.05,
        },
    )


def test_rank_items_drops_blocked_keywords() -> None:
    items = [
        Item(
            source="rss:Example",
            source_id="bad",
            title="lottery result",
            url="https://example.com/bad",
            published_at="2026-05-28T00:00:00+00:00",
            summary="blocked content",
            category="technology",
        ),
        Item(
            source="rss:OpenAI News",
            source_id="good",
            title="AI developer tools",
            url="https://openai.com/news/good",
            published_at="2026-05-28T00:00:00+00:00",
            summary="Python and AI update",
            category="ai",
        ),
    ]

    ranked = rank_items(items, preferences(), date(2026, 5, 28))

    assert [item.source_id for item in ranked] == ["good"]


def test_source_quality_prefers_configured_domain() -> None:
    prefs = preferences()
    preferred = Item(
        source="rss:OpenAI News",
        source_id="preferred",
        title="AI update",
        url="https://openai.com/news/item",
    )
    ordinary = Item(
        source="rss:Other",
        source_id="ordinary",
        title="AI update",
        url="https://example.com/news/item",
    )

    assert source_quality(preferred, prefs) > source_quality(ordinary, prefs)
