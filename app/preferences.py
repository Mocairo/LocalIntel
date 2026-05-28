from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Preferences:
    priority_topics: list[str]
    blocked_keywords: list[str]
    preferred_languages: list[str]
    blocked_domains: list[str]
    preferred_domains: list[str]
    weights: dict[str, float]


DEFAULT_WEIGHTS = {
    "freshness": 0.35,
    "source_quality": 0.2,
    "personal_interest": 0.25,
    "popularity": 0.15,
    "source_bonus": 0.05,
}


def load_preferences(path: Path) -> Preferences:
    if not path.exists():
        return Preferences([], [], ["zh", "en"], [], [], dict(DEFAULT_WEIGHTS))
    with path.open("rb") as fh:
        values = tomllib.load(fh)
    interests = _section(values, "interests")
    domains = _section(values, "domains")
    weights = dict(DEFAULT_WEIGHTS)
    for key, value in _section(values, "weights").items():
        try:
            weights[key] = float(value)
        except (TypeError, ValueError):
            continue
    return Preferences(
        priority_topics=_list(interests.get("priority_topics")),
        blocked_keywords=_list(interests.get("blocked_keywords")),
        preferred_languages=_list(interests.get("preferred_languages")) or ["zh", "en"],
        blocked_domains=_list(domains.get("blocked")),
        preferred_domains=_list(domains.get("preferred")),
        weights=weights,
    )


def _section(values: dict[str, Any], name: str) -> dict[str, Any]:
    value = values.get(name, {})
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(row).strip() for row in value if str(row).strip()]
