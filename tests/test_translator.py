from __future__ import annotations

from app.config import Settings
from app.db import save_items
from app.models import Item
from app.translator import (
    apply_cached_translation,
    apply_translation,
    clean_translation_text,
    parse_translation_lines,
    translate_world_news,
)


def test_apply_translation_records_chinese_title_and_status() -> None:
    item = Item(
        source="gdelt",
        source_id="1",
        title="Original world title",
        url="https://example.com/1",
        summary="Original summary.",
        category="world_news",
    )

    apply_translation(item, "中文标题。中文摘要内容。", "public")

    assert item.raw["original_title"] == "Original world title"
    assert item.raw["zh_title"] == "中文标题"
    assert item.raw["translation_status"] == "public"
    assert item.ai_summary == "中文标题。中文摘要内容。"


def test_translation_parser_extracts_chinese_from_malformed_json_like_rows() -> None:
    items = [
        Item(source="rss", source_id="world-1", title="World one", url="https://example.com/1", category="world_news"),
        Item(source="rss", source_id="world-2", title="World two", url="https://example.com/2", category="world_news"),
    ]
    content = "\n".join(
        [
            '{"id":"https://example.com/1","特朗普和平委员会资金去向成谜。文章解释资金账户争议。"}',
            "item_2: 俄罗斯导弹袭击后，乌克兰咖啡店店主承诺重建。邻居协助恢复营业。",
        ]
    )

    parsed = parse_translation_lines(content, items)

    assert parsed["world-1"].startswith("特朗普和平委员会资金去向成谜")
    assert parsed["world-2"].startswith("俄罗斯导弹袭击后")


def test_translation_cleaner_handles_structured_values() -> None:
    text = clean_translation_text({"translation": "美国称打击伊朗雷达阵地。科威特报告遭袭。"})

    assert text == "美国称打击伊朗雷达阵地。科威特报告遭袭。"


def test_translation_cleaner_handles_dirty_cached_title() -> None:
    text = clean_translation_text('{"id":"https://example.com/1": "美国称打击伊朗雷达阵地，科威特报告遭袭')

    assert text == "美国称打击伊朗雷达阵地，科威特报告遭袭"


def test_translation_cleaner_keeps_clean_chinese_sentence_intact() -> None:
    text = clean_translation_text("特朗普和平委员会的资金不在世界银行账户，去向成谜。")

    assert text == "特朗普和平委员会的资金不在世界银行账户，去向成谜。"


def test_cached_llm_translation_is_cleaned_before_reuse(tmp_path) -> None:
    old_item = Item(
        source="rss",
        source_id="world-1",
        title="World one",
        url="https://example.com/1",
        summary="News.",
        category="world_news",
        ai_summary='{"id":"https://example.com/1","特朗普和平委员会资金去向成谜。文章解释资金账户争议。"}',
        raw={"translation_status": "llm", "original_title": "World one"},
    )
    db_path = tmp_path / "data" / "intel.sqlite"
    save_items(db_path, [old_item])
    item = Item(source="rss", source_id="world-1", title="World one", url="https://example.com/1", summary="News.", category="world_news")

    assert apply_cached_translation(db_path, item)
    assert item.ai_summary == "特朗普和平委员会资金去向成谜。文章解释资金账户争议。"
    assert item.raw["zh_title"] == "特朗普和平委员会资金去向成谜"


def test_public_translation_caps_timeout_and_falls_back(monkeypatch, tmp_path) -> None:
    seen_timeouts: list[int] = []

    def fake_public_api(title: str, summary: str, timeout: int) -> str:
        seen_timeouts.append(timeout)
        return ""

    monkeypatch.setattr("app.translator.translate_with_public_api", fake_public_api)
    settings = Settings(
        root=tmp_path,
        values={
            "translation": {
                "enabled": True,
                "provider": "public",
                "max_items": 2,
                "timeout_seconds": 45,
            }
        },
    )
    items = [
        Item(source="gdelt", source_id="1", title="World one", url="https://example.com/1", summary="News.", category="world_news"),
        Item(source="gdelt", source_id="2", title="World two", url="https://example.com/2", summary="More news.", category="world_news"),
    ]

    error = translate_world_news(settings, items)

    assert error == ""
    assert seen_timeouts == [6, 6]
    assert all(item.ai_summary for item in items)


def test_public_translation_stops_after_failure_budget(monkeypatch, tmp_path) -> None:
    called_titles: list[str] = []

    def fake_public_api(title: str, summary: str, timeout: int) -> str:
        called_titles.append(title)
        return ""

    monkeypatch.setattr("app.translator.translate_with_public_api", fake_public_api)
    settings = Settings(
        root=tmp_path,
        values={
            "translation": {
                "enabled": True,
                "provider": "public",
                "max_items": 3,
                "public_max_failures": 1,
                "public_timeout_seconds": 4,
            }
        },
    )
    items = [
        Item(source="gdelt", source_id="1", title="World one", url="https://example.com/1", summary="News.", category="world_news"),
        Item(source="gdelt", source_id="2", title="World two", url="https://example.com/2", summary="More news.", category="world_news"),
        Item(source="rss", source_id="3", title="World three", url="https://example.com/3", summary="Last news.", category="world_news"),
    ]

    translate_world_news(settings, items)

    assert called_titles == ["World one"]
    assert all(item.ai_summary for item in items)
    assert all(item.raw["translation_status"] == "local_fallback" for item in items)


def test_default_translation_uses_llm_batches_and_retries_local_fallback_cache(monkeypatch, tmp_path) -> None:
    old_item = Item(
        source="rss",
        source_id="world-1",
        title="World one",
        url="https://example.com/1",
        summary="News.",
        category="world_news",
        ai_summary="World one。来源：News.",
        raw={"translation_status": "local_fallback", "original_title": "World one"},
    )
    save_items(tmp_path / "data" / "intel.sqlite", [old_item])
    items = [
        Item(source="rss", source_id="world-1", title="World one", url="https://example.com/1", summary="News.", category="world_news"),
        Item(source="rss", source_id="world-2", title="World two", url="https://example.com/2", summary="More news.", category="world_news"),
    ]
    seen_batches: list[list[str]] = []

    def fake_translate_batch(base_url: str, api_key: str, model: str, batch: list[Item], timeout: int) -> dict[str, str]:
        seen_batches.append([item.source_id for item in batch])
        return {item.source_id: f"中文标题{index}。中文摘要{index}。" for index, item in enumerate(batch, 1)}

    monkeypatch.setenv("TEST_LLM_KEY", "token")
    monkeypatch.setenv("TEST_LLM_BASE", "https://llm.example/v1")
    monkeypatch.setattr("app.translator.translate_batch", fake_translate_batch)
    settings = Settings(
        root=tmp_path,
        values={
            "app": {"data_dir": "data"},
            "llm": {"api_key_env": "TEST_LLM_KEY", "base_url_env": "TEST_LLM_BASE", "model": "test-model"},
            "translation": {"enabled": True, "max_items": 5, "batch_size": 1, "model": "test-model"},
        },
    )

    error = translate_world_news(settings, items, "2026-06-01")

    assert error == ""
    assert seen_batches == [["world-1"], ["world-2"]]
    assert all(item.raw["translation_status"] == "llm" for item in items)
    assert items[0].ai_summary == "中文标题1。中文摘要1。"
