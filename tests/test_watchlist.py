from __future__ import annotations

from app.db import init_db, load_watch_radar, load_watch_radar_history, load_watch_target_detail, record_watch_radar
from app.models import Item
from app.watchlist import build_local_watch_radar, load_watchlist, target_matches


def test_load_watchlist_reads_enabled_targets(tmp_path) -> None:
    path = tmp_path / "interests.toml"
    path.write_text(
        """
[[watchlist]]
id = "agent"
name = "AI Agent"
type = "topic"
keywords = ["agent", "coding agent"]
description = "跟踪智能体和编程代理"
enabled = true

[[watchlist]]
id = "empty"
name = "空关键词"
type = "topic"
keywords = []
enabled = true

[[watchlist]]
id = "disabled"
name = "禁用项"
type = "topic"
keywords = ["hidden"]
enabled = false
""".strip()
        + "\n",
        encoding="utf-8",
    )

    targets = load_watchlist(path)

    assert len(targets) == 1
    assert targets[0].id == "agent"
    assert targets[0].keywords == ["agent", "coding agent"]


def test_build_local_watch_radar_matches_items_and_keeps_quiet_targets(tmp_path) -> None:
    path = tmp_path / "interests.toml"
    path.write_text(
        """
[[watchlist]]
id = "agent"
name = "AI Agent"
type = "topic"
keywords = ["agent"]
description = "跟踪智能体"

[[watchlist]]
id = "chips"
name = "芯片产业"
type = "topic"
keywords = ["semiconductor"]
description = "跟踪芯片供应链"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    targets = load_watchlist(path)
    items = [
        Item(
            source="github_trending",
            source_id="repo-1",
            title="New coding agent framework",
            url="https://github.com/example/agent",
            summary="A useful AI agent framework",
            rank_score=91.5,
            tags=["agent"],
        )
    ]

    rows = build_local_watch_radar(targets, items, "2026-05-29")

    assert rows[0]["target_id"] == "agent"
    assert rows[0]["status"] == "active"
    assert rows[0]["match_count"] == 1
    assert rows[0]["item_title"] == "New coding agent framework"
    assert rows[1]["target_id"] == "chips"
    assert rows[1]["status"] == "quiet"
    assert rows[1]["match_count"] == 0
    assert rows[1]["generation"] == "local_rule"
    assert "建议关键词" in rows[1]["summary"]


def test_watch_radar_cache_round_trip(tmp_path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)
    rows = [
        {
            "target_id": "agent",
            "name": "AI Agent",
            "type": "topic",
            "status": "active",
            "summary": "今天出现了新的 Agent 项目。",
            "action": "立即看",
            "confidence": 0.86,
            "match_count": 2,
            "item_hash": "hash-1",
            "item_title": "Agent project",
            "source": "github_trending",
            "url": "https://example.com/agent",
            "score": 93.0,
        },
        {
            "target_id": "chips",
            "name": "芯片产业",
            "type": "topic",
            "status": "quiet",
            "summary": "今日暂无明显动向。",
            "action": "持续观察",
            "confidence": 0.0,
            "match_count": 0,
            "item_hash": "",
            "item_title": "",
            "source": "",
            "url": "",
            "score": 0,
        },
    ]

    record_watch_radar(db_path, "2026-05-29", rows)

    cached = load_watch_radar(db_path, "2026-05-29")

    assert [row["target_id"] for row in cached] == ["agent", "chips"]
    assert cached[0]["summary"] == "今天出现了新的 Agent 项目。"


def test_watch_radar_history_groups_recent_dates_by_target(tmp_path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)

    record_watch_radar(
        db_path,
        "2026-05-27",
        [
            watch_row("agent", "AI Agent", "quiet", 0, 0.0, 0),
            watch_row("rag", "RAG", "active", 1, 0.6, 22),
        ],
    )
    record_watch_radar(
        db_path,
        "2026-05-28",
        [
            watch_row("agent", "AI Agent", "active", 3, 0.8, 80),
            watch_row("rag", "RAG", "quiet", 0, 0.0, 0),
        ],
    )
    record_watch_radar(
        db_path,
        "2026-05-29",
        [
            watch_row("agent", "AI Agent", "active", 5, 0.9, 91),
            watch_row("chips", "Chips", "quiet", 0, 0.0, 0),
        ],
    )

    history = load_watch_radar_history(db_path, days=2)

    assert [row["target_id"] for row in history] == ["agent", "chips", "rag"]
    assert history[0]["latest_status"] == "active"
    assert history[0]["active_days"] == 2
    assert history[0]["total_matches"] == 8
    assert [point["report_date"] for point in history[0]["history"]] == ["2026-05-28", "2026-05-29"]
    assert [point["match_count"] for point in history[0]["history"]] == [3, 5]
    assert history[1]["latest_status"] == "quiet"
    assert history[1]["history"] == [
        {"report_date": "2026-05-29", "status": "quiet", "match_count": 0, "confidence": 0.0}
    ]


def test_watch_target_detail_returns_recent_records_for_one_target(tmp_path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)

    record_watch_radar(
        db_path,
        "2026-05-27",
        [
            watch_row("agent", "AI Agent", "quiet", 0, 0.0, 0),
            watch_row("rag", "RAG", "active", 2, 0.7, 50),
        ],
    )
    record_watch_radar(
        db_path,
        "2026-05-28",
        [watch_row("agent", "AI Agent", "active", 3, 0.8, 80)],
    )
    record_watch_radar(
        db_path,
        "2026-05-29",
        [watch_row("agent", "AI Agent", "active", 5, 0.9, 91)],
    )

    detail = load_watch_target_detail(db_path, "agent", days=2)

    assert detail["target"] == {
        "target_id": "agent",
        "name": "AI Agent",
        "type": "topic",
        "latest_status": "active",
        "latest_action": "立即看",
        "generation": "local_rule",
        "latest_confidence": 0.9,
        "latest_match_count": 5,
        "latest_report_date": "2026-05-29",
        "active_days": 2,
        "total_matches": 8,
        "max_confidence": 0.9,
    }
    assert [row["report_date"] for row in detail["records"]] == ["2026-05-29", "2026-05-28"]
    assert [row["target_id"] for row in detail["records"]] == ["agent", "agent"]
    assert detail["records"][0]["item_title"] == "AI Agent"
    assert load_watch_target_detail(db_path, "missing") == {}


def test_short_ascii_keywords_match_whole_words_only() -> None:
    target = load_watchlist_from_text(
        """
[[watchlist]]
id = "rag"
name = "RAG"
type = "topic"
keywords = ["RAG"]
"""
    )[0]

    assert not target_matches(
        target,
        Item(
            source="rss",
            source_id="storage",
            title="New storage engine released",
            url="https://example.com/storage",
            summary="A database storage update.",
        ),
    )
    assert target_matches(
        target,
        Item(
            source="arxiv",
            source_id="rag",
            title="RAG benchmark update",
            url="https://example.com/rag",
            summary="Retrieval augmented generation benchmark.",
        ),
    )


def load_watchlist_from_text(text: str):
    from tempfile import TemporaryDirectory
    from pathlib import Path

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "interests.toml"
        path.write_text(text.strip() + "\n", encoding="utf-8")
        return load_watchlist(path)


def watch_row(
    target_id: str,
    name: str,
    status: str,
    match_count: int,
    confidence: float,
    score: float,
) -> dict[str, object]:
    return {
        "target_id": target_id,
        "name": name,
        "type": "topic",
        "status": status,
        "summary": f"{name} {status}",
        "action": "立即看" if status == "active" else "持续观察",
        "confidence": confidence,
        "match_count": match_count,
        "item_hash": f"{target_id}-{status}",
        "item_title": name,
        "source": "github_trending" if match_count else "",
        "url": f"https://example.com/{target_id}" if match_count else "",
        "score": score,
    }
