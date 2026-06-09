from __future__ import annotations

import json

from app.analyzer import (
    build_llm_briefing_data,
    compute_trend_signals,
    local_fallback_briefing,
    text_tokens,
)
from app.db import init_db, load_intel_briefing, save_intel_briefing


def test_text_tokens_extracts_english_keywords() -> None:
    tokens = text_tokens("Claude Code agent framework for coding automation")
    assert "claude" in tokens
    assert "agent" in tokens
    assert "framework" in tokens


def test_text_tokens_extracts_chinese_keywords() -> None:
    tokens = text_tokens("人工智能 模型部署 本地推理")
    assert len(tokens) >= 1


def test_text_tokens_excludes_stopwords() -> None:
    tokens = text_tokens("about this that these those which while with your")
    assert not tokens


def test_compute_trend_signals_new_when_no_history() -> None:
    today_clusters = [
        {"cluster_id": "c1", "title": "AI Agent framework", "explanation": "coding agent", "score": 80, "category": "ai"},
    ]
    signals = compute_trend_signals(today_clusters, {}, "2026-06-06")
    assert len(signals) == 1
    assert signals[0]["trend"] == "new"
    assert signals[0]["cluster_id"] == "c1"


def test_compute_trend_signals_sustained_when_frequent() -> None:
    today_clusters = [
        {"cluster_id": "c1", "title": "AI Agent coding framework", "explanation": "agent coding tools", "score": 80, "category": "ai"},
    ]
    history = {
        "2026-06-05": [{"cluster_id": "h1", "title": "AI Agent coding framework", "explanation": "agent coding", "score": 70, "category": "ai"}],
        "2026-06-04": [{"cluster_id": "h2", "title": "Agent coding tools update", "explanation": "agent framework", "score": 65, "category": "ai"}],
        "2026-06-03": [{"cluster_id": "h3", "title": "AI Agent framework new", "explanation": "agent coding automation", "score": 60, "category": "ai"}],
        "2026-06-02": [{"cluster_id": "h4", "title": "Coding agent tools", "explanation": "agent framework coding", "score": 55, "category": "ai"}],
        "2026-06-01": [{"cluster_id": "h5", "title": "Agent coding framework", "explanation": "agent automation", "score": 50, "category": "ai"}],
    }
    signals = compute_trend_signals(today_clusters, history, "2026-06-06")
    assert signals[0]["trend"] == "sustained"


def test_compute_trend_signals_fading_when_recent_absent() -> None:
    today_clusters = [
        {"cluster_id": "c1", "title": "Quantum computing breakthrough", "explanation": "quantum processor error correction", "score": 80, "category": "ai"},
    ]
    history = {
        "2026-06-05": [{"cluster_id": "h1", "title": "Blockchain decentralized finance protocol", "explanation": "blockchain ethereum defi", "score": 70, "category": "ai"}],
        "2026-06-04": [{"cluster_id": "h2", "title": "Blockchain smart contract deployment", "explanation": "ethereum blockchain protocol", "score": 65, "category": "ai"}],
        "2026-06-03": [{"cluster_id": "h3", "title": "Blockchain decentralized applications", "explanation": "ethereum blockchain smart contract", "score": 60, "category": "ai"}],
        "2026-06-02": [{"cluster_id": "h4", "title": "Quantum computing breakthrough in error correction", "explanation": "quantum processor", "score": 70, "category": "ai"}],
        "2026-06-01": [{"cluster_id": "h5", "title": "Quantum processor error correction advances", "explanation": "quantum computing", "score": 65, "category": "ai"}],
    }
    signals = compute_trend_signals(today_clusters, history, "2026-06-06")
    assert signals[0]["trend"] == "fading"


def test_compute_trend_signals_rising_when_partial_overlap() -> None:
    today_clusters = [
        {"cluster_id": "c1", "title": "RAG vector database", "explanation": "retrieval augmented generation", "score": 80, "category": "ai"},
    ]
    history = {
        "2026-06-03": [{"cluster_id": "h1", "title": "RAG vector search update", "explanation": "rag retrieval", "score": 70, "category": "ai"}],
    }
    signals = compute_trend_signals(today_clusters, history, "2026-06-06")
    assert signals[0]["trend"] == "rising"


def test_build_llm_briefing_data_structures_payload() -> None:
    today_clusters = [
        {"cluster_id": "c1", "title": "AI Agent", "category": "ai", "score": 80, "size": 3, "explanation": "agent framework"},
    ]
    signals = [{"cluster_id": "c1", "trend": "new", "note": "新信号"}]
    alerts = [{"title": "重要发布", "detail": "Claude Code 4.0", "confidence": 0.9}]
    watch_radar = [{"name": "AI Agent", "status": "active", "match_count": 3, "confidence": 0.8}]

    payload = build_llm_briefing_data(today_clusters, signals, alerts, watch_radar)
    assert len(payload) == 3
    assert payload[0]["role"] == "clusters"
    assert payload[0]["items"][0]["trend"] == "new"
    assert payload[1]["role"] == "alerts"
    assert payload[2]["role"] == "watch_radar"


def test_local_fallback_briefing_produces_headline() -> None:
    today_clusters = [
        {"cluster_id": "c1", "title": "AI Agent framework", "category": "ai", "score": 80, "size": 3, "explanation": "agent"},
        {"cluster_id": "c2", "title": "半导体政策收紧", "category": "world_news", "score": 70, "size": 2, "explanation": "芯片管制"},
    ]
    signals = [
        {"cluster_id": "c1", "trend": "new", "note": "新信号"},
        {"cluster_id": "c2", "trend": "rising", "note": "升温"},
    ]
    watch_radar = [{"name": "AI Agent", "status": "active", "match_count": 3, "confidence": 0.8}]

    result = local_fallback_briefing(today_clusters, signals, watch_radar)
    assert result["headline"]
    assert result["analysis"]
    assert result["watch_digest"]
    assert result["model"] == "local_rule"


def test_local_fallback_briefing_handles_empty_clusters() -> None:
    result = local_fallback_briefing([], [], [])
    assert "暂无" in result["headline"]
    assert result["model"] == "local_rule"


def test_save_and_load_intel_briefing(tmp_path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)
    signals = [{"cluster_id": "c1", "trend": "new", "note": "新信号"}]
    save_intel_briefing(
        db_path,
        "2026-06-06",
        headline="AI Agent 生态加速整合",
        analysis="今天信号显示...",
        signals=signals,
        watch_digest="AI Agent 活跃",
        generation="llm",
        model="gpt-5.4",
    )

    result = load_intel_briefing(db_path, "2026-06-06")
    assert result["headline"] == "AI Agent 生态加速整合"
    assert result["analysis"] == "今天信号显示..."
    assert result["signals"] == signals
    assert result["watch_digest"] == "AI Agent 活跃"
    assert result["generation"] == "llm"
    assert result["model"] == "gpt-5.4"


def test_load_intel_briefing_returns_empty_for_missing(tmp_path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)
    result = load_intel_briefing(db_path, "2099-01-01")
    assert result == {}
