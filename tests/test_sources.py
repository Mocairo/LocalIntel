from __future__ import annotations

from datetime import date

from app.http import FetchError
from app.models import Item
from app.sources import fetch_arxiv


def test_fetch_arxiv_falls_back_to_latest_after_dated_timeout(monkeypatch) -> None:
    calls: list[str] = []

    def fake_query(search_query: str, limit: int, timeout: int, delay: float) -> list[Item]:
        calls.append(search_query)
        if "submittedDate" in search_query:
            raise FetchError("Network error for arxiv: The read operation timed out")
        return [
            Item(
                source="arxiv",
                source_id="paper-1",
                title="Fallback paper",
                url="https://arxiv.org/abs/1",
                summary="A fallback result.",
                category="ai",
            )
        ]

    monkeypatch.setattr("app.sources.fetch_arxiv_query_with_retry", fake_query)

    items = fetch_arxiv(
        {"categories": ["cs.AI"], "limit": 1, "fallback_latest": True, "delay_seconds": 0},
        timeout=1,
        since=date(2026, 5, 28),
        run_date=date(2026, 5, 29),
    )

    assert [item.title for item in items] == ["Fallback paper"]
    assert len(calls) == 2
