from __future__ import annotations

import os
from pathlib import Path

from app.config import load_settings


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
timezone = "Asia/Shanghai"
data_dir = "data"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_load_settings_does_not_load_env_example_when_env_is_missing(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    (tmp_path / ".env.example").write_text("MIMO_API_KEY=example-key\n", encoding="utf-8")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)

    load_settings(config_path, tmp_path / ".env")

    assert "MIMO_API_KEY" not in os.environ
