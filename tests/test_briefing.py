from __future__ import annotations

from app.db import dashboard_briefing, init_db, record_clusters, record_llm_job, record_run, save_items
from app.dedupe import item_hash
from app.models import Item


def test_dashboard_briefing_prefers_llm_status_and_chinese_world_news(tmp_path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)
    world = Item(
        source="gdelt",
        source_id="world-1",
        title="Original world title",
        url="https://example.com/world",
        summary="Original summary",
        category="world_news",
        rank_score=88,
        ai_summary="中文摘要：全球市场出现新的政策风险。",
        why="这会影响宏观环境和供应链判断。",
        importance=4,
        raw={
            "original_title": "Original world title",
            "zh_title": "全球政策风险升温",
            "translation_status": "public",
        },
    )
    tech = Item(
        source="github_trending",
        source_id="repo-1",
        title="Agent radar",
        url="https://example.com/repo",
        summary="Agent tool",
        category="open_source",
        rank_score=92,
        top_reason="GitHub Trending 高热度。",
        importance=5,
    )
    items = [tech, world]
    save_items(db_path, items)
    record_run(
        db_path,
        "2026-06-01",
        items,
        {"raw_total": 2, "deduped_total": 2, "inserted": 2, "source_counts": {"gdelt": 1}},
        "LLM 模型：test\n\n今日最重要的是开源工具和全球政策风险。",
    )
    record_clusters(
        db_path,
        "2026-06-01",
        [
            {
                "cluster_id": "cluster-1",
                "title": "Agent radar",
                "category": "open_source",
                "summary": "Agent tool",
                "explanation": "多个证据指向 Agent 工具升温。",
                "score": 95,
                "size": 2,
                "item_hashes": [item_hash(tech), item_hash(world)],
                "item_scores": {item_hash(tech): 92, item_hash(world): 88},
            }
        ],
    )
    record_llm_job(db_path, "2026-06-01", "daily_summary", "ok", "test-model", 2, "")

    briefing = dashboard_briefing(db_path, "2026-06-01")

    assert briefing["generation"]["mode"] == "llm"
    assert briefing["headlines"][0]["evidence_count"] == 2
    assert briefing["headlines"][0]["generation"] == "llm"
    assert "阅读" in briefing["headlines"][0]["actions"]
    assert briefing["world_news"][0]["title"] == "全球政策风险升温"
    assert briefing["world_news"][0]["original_title"] == "Original world title"
