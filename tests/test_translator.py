from __future__ import annotations

from app.config import Settings
from app.models import Item
from app.translator import translate_world_news


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
