from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Settings:
    root: Path
    values: dict[str, Any]

    def section(self, name: str) -> dict[str, Any]:
        value = self.values.get(name, {})
        return value if isinstance(value, dict) else {}

    def app_path(self, key: str) -> Path:
        raw = self.section("app").get(key, key)
        path = Path(str(raw))
        return path if path.is_absolute() else self.root / path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings(config_path: str | Path, env_path: str | Path | None = None) -> Settings:
    config = Path(config_path).resolve()
    if env_path is not None:
        env = Path(env_path)
        if not env.is_absolute():
            env = config.parent / env
        if env.exists():
            load_dotenv(env)
    with config.open("rb") as fh:
        values = tomllib.load(fh)
    return Settings(root=config.parent, values=values)
