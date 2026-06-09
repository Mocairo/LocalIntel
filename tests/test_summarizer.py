from __future__ import annotations

from app.config import Settings
from app.models import Item
from app.summarizer import build_llm_summary, configured_model_candidates, parse_json_object


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


def test_model_candidates_use_configured_openai_model_first() -> None:
    candidates = configured_model_candidates(
        {"model": "gpt-5.4", "model_candidates": ["gpt-5.4-mini", "gpt-5.4"]},
        "gpt-5.4",
    )

    assert candidates == ["gpt-5.4", "gpt-5.4-mini"]


def test_parse_json_object_handles_markdown_fence_and_extra_text() -> None:
    parsed = parse_json_object(
        '模型输出如下：\n```json\n{"overview":"中文摘要","items":[]}\n```\n请查收。'
    )

    assert parsed == {"overview": "中文摘要", "items": []}


def test_plain_text_llm_summary_counts_as_model_summary(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr(
        "app.summarizer.request_summary",
        lambda *args, **kwargs: {
            "choices": [
                {
                    "message": {"content": "今日重点是全球时事和开源工具更新。"},
                    "finish_reason": "stop",
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
        "2026-06-01",
    )

    assert summary.startswith("LLM 模型：test-model")
    assert "今日重点是全球时事和开源工具更新。" in summary
