from __future__ import annotations

from app.clusters import build_clusters
from app.models import Item


def test_build_clusters_groups_related_non_github_items() -> None:
    items = [
        Item(
            source="rss:News A",
            source_id="a",
            title="Iran energy supply risk grows",
            url="https://example.com/a",
            summary="Iran energy supply risk affects oil market and shipping routes",
            category="world_news",
            rank_score=90,
        ),
        Item(
            source="rss:News B",
            source_id="b",
            title="Iran oil market faces supply pressure",
            url="https://example.com/b",
            summary="Iran energy supply pressure affects oil market and shipping routes",
            category="world_news",
            rank_score=80,
        ),
    ]

    clusters = build_clusters(items, limit=10)

    assert len(clusters) == 1
    assert clusters[0]["size"] == 2


def test_build_clusters_keeps_github_trending_items_standalone() -> None:
    items = [
        Item(
            source="github_trending",
            source_id="repo-a",
            title="agent framework",
            url="https://github.com/example/a",
            summary="agent framework for coding",
            category="open_source",
            rank_score=90,
        ),
        Item(
            source="github_trending",
            source_id="repo-b",
            title="agent framework tools",
            url="https://github.com/example/b",
            summary="agent framework for coding",
            category="open_source",
            rank_score=80,
        ),
    ]

    clusters = build_clusters(items, limit=10)

    assert len(clusters) == 2
    assert {cluster["size"] for cluster in clusters} == {1}
