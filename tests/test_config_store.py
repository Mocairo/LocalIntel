from __future__ import annotations

from pathlib import Path

from app.config import load_settings
from app.config_store import read_ui_config, update_config


def write_base_files(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.toml"
    env_path = tmp_path / ".env"
    interests_path = tmp_path / "interests.toml"
    config_path.write_text(
        """
[app]
timezone = "Asia/Shanghai"
daily_time = "08:30"
days_back = 1
data_dir = "data"
report_dir = "reports"
log_dir = "logs"
interests_file = "interests.toml"

[github]
enabled = true
limit = 10
trending_since = "daily"
trending_languages = [""]

[llm]
enabled = true
model = "gpt-5.4"
max_items = 3
max_tokens = 4000
""".strip()
        + "\n",
        encoding="utf-8",
    )
    env_path.write_text("", encoding="utf-8")
    interests_path.write_text(
        """
[interests]
priority_topics = []
blocked_keywords = []
preferred_languages = ["zh", "en"]

[domains]
blocked = []
preferred = []

[weights]
freshness = 0.35
source_quality = 0.2
personal_interest = 0.25
popularity = 0.15
source_bonus = 0.05

[[watchlist]]
id = "agent"
name = "AI Agent"
type = "topic"
keywords = ["agent"]
description = "跟踪智能体"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path, env_path


def test_update_config_normalizes_values_and_ignores_unknown_keys(tmp_path: Path) -> None:
    config_path, env_path = write_base_files(tmp_path)

    settings = update_config(
        config_path,
        env_path,
        {
            "app": {"days_back": "0", "unknown": "value"},
            "github": {"limit": "12", "trending_since": "yearly"},
            "llm": {"max_items": "40", "max_tokens": "8000"},
            "interests": {"priority_topics": ["AI"]},
        },
    )

    assert settings.section("app")["days_back"] == 1
    assert "unknown" not in settings.section("app")
    assert settings.section("github")["limit"] == 12
    assert settings.section("github")["trending_since"] == "daily"
    assert settings.section("llm")["max_items"] == 40
    assert settings.section("llm")["max_tokens"] == 8000
    assert "watchlist" in env_path.with_name("interests.toml").read_text(encoding="utf-8")


def test_read_ui_config_includes_watchlist(tmp_path: Path) -> None:
    config_path, env_path = write_base_files(tmp_path)
    settings = load_settings(config_path, env_path)

    ui_config = read_ui_config(settings)

    assert ui_config["interests"]["watchlist"] == [
        {
            "id": "agent",
            "name": "AI Agent",
            "type": "topic",
            "enabled": True,
            "keywords": ["agent"],
            "description": "跟踪智能体",
        }
    ]


def test_update_config_saves_watchlist_and_filters_invalid_rows(tmp_path: Path) -> None:
    config_path, env_path = write_base_files(tmp_path)

    settings = update_config(
        config_path,
        env_path,
        {
            "interests": {
                "priority_topics": ["AI"],
                "watchlist": [
                    {
                        "id": "",
                        "name": "Model Context",
                        "type": "",
                        "enabled": False,
                        "keywords": ["context window", "token"],
                        "description": "关注长上下文和 token 成本",
                    },
                    {"id": "empty-keywords", "name": "空关键词", "keywords": []},
                    {"id": "empty-name", "name": "", "keywords": ["ai"]},
                ],
            }
        },
    )

    ui_config = read_ui_config(settings)

    assert ui_config["interests"]["watchlist"] == [
            {
                "id": "model-context",
                "name": "Model Context",
                "type": "topic",
                "enabled": False,
                "keywords": ["context window", "token"],
            "description": "关注长上下文和 token 成本",
        }
    ]
