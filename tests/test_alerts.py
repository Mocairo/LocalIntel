from __future__ import annotations

from app.alerts import DEFAULT_ALERT_LIMIT, normalize_llm_alerts
from app.db import dashboard_alerts, init_db, record_llm_alerts


def test_normalize_llm_alerts_limits_and_cleans_rows() -> None:
    response = {
        "alerts": [
            {
                "item_hash": "a",
                "kind": "topic_watch",
                "title": "值得关注",
                "detail": "与当前关注方向有关",
                "action": "立即看",
                "confidence": 1.5,
            },
            {
                "item_hash": "",
                "kind": "ignored",
                "title": "缺 hash",
                "detail": "应被忽略",
            },
            *[
                {
                    "item_hash": f"extra-{index}",
                    "kind": "github_spike",
                    "title": "额外提醒",
                    "detail": "超过上限",
                    "confidence": 0.5,
                }
                for index in range(5)
            ],
        ]
    }

    alerts = normalize_llm_alerts(response, limit=DEFAULT_ALERT_LIMIT)

    assert DEFAULT_ALERT_LIMIT == 6
    assert len(alerts) == 6
    assert alerts[0]["item_hash"] == "a"
    assert alerts[0]["confidence"] == 1.0
    assert alerts[0]["action"] == "立即看"


def test_dashboard_alerts_prefers_cached_llm_alerts(tmp_path) -> None:
    db_path = tmp_path / "intel.sqlite"
    init_db(db_path)
    record_llm_alerts(
        db_path,
        "2026-05-29",
        [
            {
                "kind": "llm_watch",
                "title": "模型判断",
                "detail": "这是模型缓存的提醒",
                "action": "观察",
                "confidence": 0.82,
                "item_hash": "hash-1",
                "item_title": "重要项目",
                "source": "github_trending",
                "url": "https://example.com/item",
                "score": 91.5,
            }
        ],
    )

    alerts = dashboard_alerts(db_path, "2026-05-29")

    assert alerts == [
        {
            "kind": "llm_watch",
            "title": "模型判断",
            "detail": "这是模型缓存的提醒",
            "action": "观察",
            "confidence": 0.82,
            "item_hash": "hash-1",
            "item_title": "重要项目",
            "source": "github_trending",
            "url": "https://example.com/item",
            "score": 91.5,
        }
    ]
