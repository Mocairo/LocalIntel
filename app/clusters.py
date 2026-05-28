from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.dedupe import item_hash
from app.models import Item


STOPWORDS = {
    "about",
    "after",
    "also",
    "amid",
    "from",
    "have",
    "into",
    "more",
    "over",
    "says",
    "than",
    "that",
    "their",
    "this",
    "with",
    "will",
    "your",
    "github",
    "release",
    "paper",
    "using",
    "based",
}

CATEGORY_LABELS = {
    "ai": "AI 与论文",
    "open_source": "开源项目",
    "technology": "技术热点",
    "programming": "编程与工程",
    "world_news": "全球时事",
    "general": "其他",
}

CATEGORY_ACTIONS = {
    "ai": "适合判断是否需要继续跟踪研究方向或产品能力变化。",
    "open_source": "适合评估是否加入你的工具箱、项目观察清单或后续试用清单。",
    "technology": "适合快速判断是否影响日常开发、技术选型或行业认知。",
    "programming": "适合沉淀到工程实践、库版本升级或开发习惯里。",
    "world_news": "适合了解外部环境变化，并判断是否会影响技术、市场或个人决策。",
    "general": "适合留作背景材料，必要时再深入阅读。",
}


@dataclass(slots=True)
class WorkingCluster:
    cluster_id: str
    title: str
    category: str
    summary: str
    explanation: str
    score: float
    item_hashes: list[str]
    item_scores: dict[str, float]
    tokens: set[str] = field(default_factory=set)


def build_clusters(items: list[Item], limit: int = 80) -> list[dict[str, object]]:
    clusters: list[WorkingCluster] = []
    for item in sorted(items, key=lambda row: row.rank_score, reverse=True)[:limit]:
        current_hash = item_hash(item)
        tokens = item_tokens(item)
        target = best_cluster(item, tokens, clusters)
        if target is None:
            seed = stable_cluster_id(item, current_hash)
            clusters.append(
                WorkingCluster(
                    cluster_id=seed,
                    title=item.title,
                    category=item.category or "general",
                    summary=item.ai_summary or item.compact_summary(180),
                    explanation=explain_cluster(item, 1),
                    score=float(item.rank_score),
                    item_hashes=[current_hash],
                    item_scores={current_hash: float(item.rank_score)},
                    tokens=set(tokens),
                )
            )
            continue
        target.item_hashes.append(current_hash)
        target.item_scores[current_hash] = float(item.rank_score)
        target.tokens.update(tokens)
        target.score = round(target.score + item.rank_score * 0.45, 2)
        if not target.summary and (item.ai_summary or item.summary):
            target.summary = item.ai_summary or item.compact_summary(180)
        target.explanation = explain_cluster_from_parts(target.title, target.category, target.summary, len(target.item_hashes))

    return [
        {
            "cluster_id": cluster.cluster_id,
            "title": cluster.title,
            "category": cluster.category,
            "summary": cluster.summary,
            "explanation": cluster.explanation,
            "score": round(cluster.score, 2),
            "size": len(cluster.item_hashes),
            "item_hashes": cluster.item_hashes,
            "item_scores": cluster.item_scores,
        }
        for cluster in sorted(clusters, key=lambda row: row.score, reverse=True)
    ]


def best_cluster(item: Item, tokens: set[str], clusters: list[WorkingCluster]) -> WorkingCluster | None:
    if item.source in {"github_trending", "github_release"}:
        return None
    best: WorkingCluster | None = None
    best_score = 0.0
    for cluster in clusters:
        if cluster.category != (item.category or "general"):
            continue
        score = similarity(tokens, cluster.tokens)
        if score > best_score:
            best = cluster
            best_score = score
    threshold = 0.34 if item.category == "world_news" else 0.4
    return best if best is not None and best_score >= threshold else None


def explain_cluster(item: Item, size: int) -> str:
    summary = item.ai_summary or item.why or item.top_reason or item.compact_summary(120)
    return explain_cluster_from_parts(item.title, item.category, summary, size)


def explain_cluster_from_parts(title: str, category: str, summary: str, size: int) -> str:
    label = CATEGORY_LABELS.get(category or "general", category or "其他")
    action = CATEGORY_ACTIONS.get(category or "general", CATEGORY_ACTIONS["general"])
    clue = " ".join((summary or title).split())[:140]
    if size > 1:
        return f"这条主线把 {size} 条相关内容聚在一起，主题属于{label}。{clue} {action}"
    return f"这是一条{label}主线。{clue} {action}"


def item_tokens(item: Item) -> set[str]:
    text = " ".join([item.title, item.ai_summary, item.summary, item.content])
    tokens = {
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.casefold())
        if token not in STOPWORDS and len(token) > 3
    }
    if not tokens:
        tokens = {token for token in re.findall(r"[\u4e00-\u9fff]{2,}", text) if len(token) >= 2}
    return set(list(tokens)[:30])


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    if overlap < 2:
        return 0.0
    return overlap / len(left | right)


def stable_cluster_id(item: Item, current_hash: str) -> str:
    seed = f"{item.category}:{current_hash}:{item.title[:80]}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
