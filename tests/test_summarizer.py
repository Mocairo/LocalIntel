from __future__ import annotations

from app.config import Settings
from app.models import Item
from app.summarizer import build_llm_summary


def test_build_llm_summary_extracts_overview_from_truncated_json(monkeypatch, tmp_path) -> None:
    settings = Settings(
        root=tmp_path,
        values={
            "app": {"data_dir": "data"},
            "llm": {
                "enabled": True,
                "api_key_env": "TEST_LLM_KEY",
                "model": "test-model",
                "model_candidates": ["test-model"],
                "max_items": 2,
                "max_tokens": 1000,
                "timeout_seconds": 1,
            },
        },
    )
    monkeypatch.setenv("TEST_LLM_KEY", "token")
    truncated_content = (
        '{\n  "overview": "今日摘要应该只显示这一句。",\n'
        '  "items": [{"hash": "abc", "zh_summary": "还没输出完"'
    )
    monkeypatch.setattr(
        "app.summarizer.request_summary",
        lambda *args, **kwargs: {
            "choices": [
                {
                    "message": {"content": truncated_content},
                    "finish_reason": "length",
                }
            ]
        },
    )

    summary = build_llm_summary(
        settings,
        [
            Item(
                source="github",
                source_id="abc",
                title="Demo project",
                url="https://example.com/demo",
                summary="A useful AI project.",
                rank_score=10,
            )
        ],
        "2026-05-29",
    )

    assert "今日摘要应该只显示这一句。" in summary
    assert '"items"' not in summary
    assert len(summary) < 160
