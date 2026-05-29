from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dedupe import item_hash
from app.models import Item
from app.ranker import select_highlights


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    hash TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    summary TEXT,
    content TEXT,
    category TEXT,
    score REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);

CREATE TABLE IF NOT EXISTS report_runs (
    report_date TEXT PRIMARY KEY,
    raw_total INTEGER NOT NULL,
    deduped_total INTEGER NOT NULL,
    inserted INTEGER NOT NULL,
    llm_summary TEXT,
    errors_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_runs (
    report_date TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    rank_score REAL NOT NULL,
    top_pick INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (report_date, item_hash)
);

CREATE INDEX IF NOT EXISTS idx_item_runs_date_rank ON item_runs(report_date, rank_score DESC);

CREATE TABLE IF NOT EXISTS user_marks (
    item_hash TEXT PRIMARY KEY,
    favorite INTEGER NOT NULL DEFAULT 0,
    ignored INTEGER NOT NULL DEFAULT 0,
    read_status TEXT NOT NULL DEFAULT 'unread',
    read_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health (
    report_date TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    count INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (report_date, source)
);

CREATE TABLE IF NOT EXISTS clusters (
    report_date TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    summary TEXT,
    explanation TEXT DEFAULT '',
    score REAL NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (report_date, cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_clusters_date_score ON clusters(report_date, score DESC);

CREATE TABLE IF NOT EXISTS cluster_items (
    report_date TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    rank_score REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (report_date, cluster_id, item_hash)
);

CREATE INDEX IF NOT EXISTS idx_cluster_items_item ON cluster_items(report_date, item_hash);

CREATE TABLE IF NOT EXISTS user_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_hash TEXT NOT NULL,
    event_type TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_events_item ON user_events(item_hash, event_type);

CREATE TABLE IF NOT EXISTS llm_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT,
    item_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_jobs_date ON llm_jobs(report_date, id DESC);

CREATE TABLE IF NOT EXISTS llm_alerts (
    report_date TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    item_title TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (report_date, item_hash, kind)
);

CREATE INDEX IF NOT EXISTS idx_llm_alerts_date ON llm_alerts(report_date, confidence DESC, score DESC);

CREATE TABLE IF NOT EXISTS watch_radar (
    report_date TEXT NOT NULL,
    target_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    match_count INTEGER NOT NULL,
    item_hash TEXT NOT NULL,
    item_title TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (report_date, target_id)
);

CREATE INDEX IF NOT EXISTS idx_watch_radar_date_score ON watch_radar(report_date, score DESC);
"""

ITEM_EXTRA_COLUMNS = {
    "rank_score": "REAL DEFAULT 0",
    "ai_summary": "TEXT DEFAULT ''",
    "why": "TEXT DEFAULT ''",
    "importance": "INTEGER DEFAULT 0",
    "bucket": "TEXT DEFAULT 'scan'",
    "tags_json": "TEXT DEFAULT '[]'",
    "top_reason": "TEXT DEFAULT ''",
    "updated_at": "TEXT",
}

CLUSTER_EXTRA_COLUMNS = {
    "explanation": "TEXT DEFAULT ''",
}

USER_MARK_EXTRA_COLUMNS = {
    "read_status": "TEXT DEFAULT 'unread'",
    "read_at": "TEXT",
}

READ_STATUSES = {"unread", "read", "later", "archived"}


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        ensure_item_columns(conn)
        ensure_cluster_columns(conn)
        ensure_user_mark_columns(conn)


def ensure_item_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    for name, definition in ITEM_EXTRA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {definition}")


def ensure_cluster_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(clusters)").fetchall()}
    for name, definition in CLUSTER_EXTRA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE clusters ADD COLUMN {name} {definition}")


def ensure_user_mark_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(user_marks)").fetchall()}
    for name, definition in USER_MARK_EXTRA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE user_marks ADD COLUMN {name} {definition}")


def save_items(path: Path, items: list[Item]) -> int:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = 0
    with sqlite3.connect(path) as conn:
        for item in items:
            cur = conn.execute(
                """
                INSERT INTO items
                    (hash, source, source_id, title, url, published_at, summary, content,
                     category, score, raw_json, created_at, rank_score, ai_summary, why,
                     importance, bucket, tags_json, top_reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hash) DO UPDATE SET
                    source=excluded.source,
                    source_id=excluded.source_id,
                    title=excluded.title,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    summary=excluded.summary,
                    content=excluded.content,
                    category=excluded.category,
                    score=excluded.score,
                    raw_json=excluded.raw_json,
                    rank_score=excluded.rank_score,
                    ai_summary=excluded.ai_summary,
                    why=excluded.why,
                    importance=excluded.importance,
                    bucket=excluded.bucket,
                    tags_json=excluded.tags_json,
                    top_reason=excluded.top_reason,
                    updated_at=excluded.updated_at
                """,
                (
                    item_hash(item),
                    item.source,
                    item.source_id,
                    item.title,
                    item.url,
                    item.published_at,
                    item.summary,
                    item.content,
                    item.category,
                    item.score,
                    json.dumps(item.raw, ensure_ascii=False),
                    now,
                    item.rank_score,
                    item.ai_summary,
                    item.why,
                    item.importance,
                    item.bucket,
                    json.dumps(item.tags, ensure_ascii=False),
                    item.top_reason,
                    now,
                ),
            )
            changed += cur.rowcount
    return changed


def record_run(path: Path, report_date: str, items: list[Item], stats: dict[str, object], llm_summary: str) -> None:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO report_runs
                (report_date, raw_total, deduped_total, inserted, llm_summary, errors_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET
                raw_total=excluded.raw_total,
                deduped_total=excluded.deduped_total,
                inserted=excluded.inserted,
                llm_summary=excluded.llm_summary,
                errors_json=excluded.errors_json,
                created_at=excluded.created_at
            """,
            (
                report_date,
                int(stats.get("raw_total", 0)),
                int(stats.get("deduped_total", 0)),
                int(stats.get("inserted", 0)),
                llm_summary,
                json.dumps(stats.get("errors", []), ensure_ascii=False),
                now,
            ),
        )
        conn.execute("DELETE FROM item_runs WHERE report_date = ?", (report_date,))
        highlights = {item_hash(item) for item in select_highlights(items, 5)}
        for item in items:
            current_hash = item_hash(item)
            conn.execute(
                """
                INSERT INTO item_runs (report_date, item_hash, rank_score, top_pick, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report_date, current_hash, item.rank_score, 1 if current_hash in highlights else 0, now),
            )
        conn.execute("DELETE FROM source_health WHERE report_date = ?", (report_date,))
        health = stats.get("source_health", {})
        if isinstance(health, dict):
            for source, row in health.items():
                if not isinstance(row, dict):
                    continue
                conn.execute(
                    """
                    INSERT INTO source_health
                        (report_date, source, status, count, duration_seconds, error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_date,
                        str(source),
                        str(row.get("status", "unknown")),
                        int(row.get("count", 0)),
                        float(row.get("duration_seconds", 0)),
                        str(row.get("error", "")),
                        now,
                    ),
                )


def record_clusters(path: Path, report_date: str, clusters: list[dict[str, Any]]) -> None:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM cluster_items WHERE report_date = ?", (report_date,))
        conn.execute("DELETE FROM clusters WHERE report_date = ?", (report_date,))
        for cluster in clusters:
            cluster_id = str(cluster.get("cluster_id") or "")
            if not cluster_id:
                continue
            item_hashes = [str(row) for row in cluster.get("item_hashes", []) if str(row)]
            conn.execute(
                """
                INSERT INTO clusters
                    (report_date, cluster_id, title, category, summary, explanation, score, size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_date,
                    cluster_id,
                    str(cluster.get("title") or "未命名事件"),
                    str(cluster.get("category") or "general"),
                    str(cluster.get("summary") or ""),
                    str(cluster.get("explanation") or ""),
                    float(cluster.get("score") or 0),
                    int(cluster.get("size") or len(item_hashes) or 1),
                    now,
                ),
            )
            for item_hash_value in item_hashes:
                conn.execute(
                    """
                    INSERT INTO cluster_items (report_date, cluster_id, item_hash, rank_score, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        report_date,
                        cluster_id,
                        item_hash_value,
                        float(cluster.get("item_scores", {}).get(item_hash_value, 0))
                        if isinstance(cluster.get("item_scores"), dict)
                        else 0,
                        now,
                    ),
                )


def list_report_dates(path: Path) -> list[str]:
    init_db(path)
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT report_date FROM report_runs ORDER BY report_date DESC").fetchall()
    return [str(row[0]) for row in rows]


def latest_report_date(path: Path) -> str:
    dates = list_report_dates(path)
    return dates[0] if dates else ""


def load_dashboard_items(
    path: Path,
    report_date: str = "",
    category: str = "",
    bucket: str = "",
    read_status: str = "",
    query: str = "",
    favorite: bool = False,
    include_ignored: bool = False,
    limit: int = 200,
) -> list[dict[str, object]]:
    init_db(path)
    if not report_date:
        report_date = latest_report_date(path)
    if not report_date:
        return []

    where = ["ir.report_date = ?"]
    params: list[object] = [report_date]
    if category:
        where.append("i.category = ?")
        params.append(category)
    if bucket:
        where.append("i.bucket = ?")
        params.append(bucket)
    if read_status:
        where.append("COALESCE(m.read_status, 'unread') = ?")
        params.append(read_status)
    elif not include_ignored:
        where.append("COALESCE(m.read_status, 'unread') <> 'archived'")
    if query:
        where.append("(i.title LIKE ? OR i.summary LIKE ? OR i.ai_summary LIKE ? OR i.why LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like, like])
    if favorite:
        where.append("COALESCE(m.favorite, 0) = 1")
    if not include_ignored:
        where.append("COALESCE(m.ignored, 0) = 0")
    params.append(limit)

    sql = f"""
        SELECT
            ir.report_date, i.hash, i.source, i.title, i.url, i.published_at, i.summary, i.category,
            i.score, i.rank_score, i.ai_summary, i.why, i.importance, i.tags_json,
            i.top_reason, i.bucket, i.raw_json, ci.cluster_id, COALESCE(c.size, 1) AS cluster_size,
            COALESCE(m.favorite, 0) AS favorite, COALESCE(m.ignored, 0) AS ignored,
            COALESCE(m.read_status, 'unread') AS read_status
        FROM item_runs ir
        JOIN items i ON i.hash = ir.item_hash
        LEFT JOIN user_marks m ON m.item_hash = i.hash
        LEFT JOIN cluster_items ci ON ci.report_date = ir.report_date AND ci.item_hash = i.hash
        LEFT JOIN clusters c ON c.report_date = ci.report_date AND c.cluster_id = ci.cluster_id
        WHERE {' AND '.join(where)}
        ORDER BY ir.rank_score DESC
        LIMIT ?
    """
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [row_to_item_dict(row) for row in rows]


def dashboard_stats(path: Path, report_date: str = "") -> dict[str, object]:
    init_db(path)
    if not report_date:
        report_date = latest_report_date(path)
    if not report_date:
        return {"report_date": "", "source_health": [], "run": {}}
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM report_runs WHERE report_date = ?", (report_date,)).fetchone()
        health = conn.execute(
            "SELECT source, status, count, duration_seconds, error FROM source_health WHERE report_date = ? ORDER BY source",
            (report_date,),
        ).fetchall()
        categories = conn.execute(
            """
            SELECT i.category, COUNT(*) AS count
            FROM item_runs ir
            JOIN items i ON i.hash = ir.item_hash
            LEFT JOIN user_marks m ON m.item_hash = i.hash
            WHERE ir.report_date = ? AND COALESCE(m.ignored, 0) = 0
            GROUP BY i.category
            ORDER BY count DESC
            """,
            (report_date,),
        ).fetchall()
        read_statuses = conn.execute(
            """
            SELECT COALESCE(m.read_status, 'unread') AS read_status, COUNT(*) AS count
            FROM item_runs ir
            JOIN items i ON i.hash = ir.item_hash
            LEFT JOIN user_marks m ON m.item_hash = i.hash
            WHERE ir.report_date = ? AND COALESCE(m.ignored, 0) = 0
            GROUP BY COALESCE(m.read_status, 'unread')
            ORDER BY count DESC
            """,
            (report_date,),
        ).fetchall()
        buckets = conn.execute(
            """
            SELECT i.bucket, COUNT(*) AS count
            FROM item_runs ir
            JOIN items i ON i.hash = ir.item_hash
            LEFT JOIN user_marks m ON m.item_hash = i.hash
            WHERE ir.report_date = ? AND COALESCE(m.ignored, 0) = 0
            GROUP BY i.bucket
            ORDER BY count DESC
            """,
            (report_date,),
        ).fetchall()
        cluster_count = conn.execute(
            "SELECT COUNT(*) FROM clusters WHERE report_date = ?",
            (report_date,),
        ).fetchone()[0]
        mark_counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END) AS favorites,
                SUM(CASE WHEN ignored = 1 THEN 1 ELSE 0 END) AS ignored
            FROM user_marks
            """,
        ).fetchone()
        llm_jobs = conn.execute(
            """
            SELECT job_type, status, model, item_count, error, created_at
            FROM llm_jobs
            WHERE report_date = ?
            ORDER BY id DESC
            LIMIT 5
            """,
            (report_date,),
        ).fetchall()
    return {
        "report_date": report_date,
        "run": dict(run) if run else {},
        "source_health": [dict(row) for row in health],
        "category_counts": [dict(row) for row in categories],
        "read_status_counts": [dict(row) for row in read_statuses],
        "bucket_counts": [dict(row) for row in buckets],
        "cluster_count": int(cluster_count or 0),
        "llm_jobs": [dict(row) for row in llm_jobs],
        "mark_counts": {
            "favorites": int((mark_counts["favorites"] if mark_counts else 0) or 0),
            "ignored": int((mark_counts["ignored"] if mark_counts else 0) or 0),
        },
    }


def dashboard_alerts(path: Path, report_date: str = "", limit: int = 6) -> list[dict[str, object]]:
    cached = load_llm_alerts(path, report_date, limit)
    if cached:
        return cached
    items = load_dashboard_items(path, report_date=report_date, include_ignored=False, limit=200)
    alerts: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        kind, title, detail = alert_for_item(item)
        if not kind:
            continue
        key = f"{kind}:{item.get('hash')}"
        if key in seen:
            continue
        seen.add(key)
        alerts.append(
            {
                "kind": kind,
                "title": title,
                "detail": detail,
                "item_hash": item.get("hash", ""),
                "item_title": item.get("title", ""),
                "source": item.get("source", ""),
                "url": item.get("url", ""),
                "score": item.get("rank_score", 0),
            }
        )
        if len(alerts) >= limit:
            break
    return alerts


def load_llm_alerts(path: Path, report_date: str = "", limit: int = 6) -> list[dict[str, object]]:
    init_db(path)
    if not report_date:
        report_date = latest_report_date(path)
    if not report_date:
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT kind, title, detail, action, confidence, item_hash, item_title, source, url, score
            FROM llm_alerts
            WHERE report_date = ?
            ORDER BY confidence DESC, score DESC
            LIMIT ?
            """,
            (report_date, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def record_llm_alerts(path: Path, report_date: str, alerts: list[dict[str, object]]) -> None:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM llm_alerts WHERE report_date = ?", (report_date,))
        for alert in alerts:
            item_hash_value = str(alert.get("item_hash") or "").strip()
            if not item_hash_value:
                continue
            conn.execute(
                """
                INSERT INTO llm_alerts
                    (report_date, item_hash, kind, title, detail, action, confidence,
                     item_title, source, url, score, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_date,
                    item_hash_value,
                    str(alert.get("kind") or "llm_watch")[:40],
                    str(alert.get("title") or "模型判断")[:120],
                    str(alert.get("detail") or "")[:500],
                    str(alert.get("action") or "观察")[:20],
                    float(alert.get("confidence") or 0),
                    str(alert.get("item_title") or "")[:300],
                    str(alert.get("source") or "")[:120],
                    str(alert.get("url") or ""),
                    float(alert.get("score") or 0),
                    now,
                ),
            )


def load_watch_radar(path: Path, report_date: str = "", limit: int = 6) -> list[dict[str, object]]:
    init_db(path)
    if not report_date:
        report_date = latest_report_date(path)
    if not report_date:
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT target_id, name, type, status, summary, action, confidence, match_count,
                   item_hash, item_title, source, url, score
            FROM watch_radar
            WHERE report_date = ?
            ORDER BY status = 'active' DESC, score DESC, match_count DESC, name
            LIMIT ?
            """,
            (report_date, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def record_watch_radar(path: Path, report_date: str, rows: list[dict[str, object]]) -> None:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM watch_radar WHERE report_date = ?", (report_date,))
        for row in rows:
            target_id = str(row.get("target_id") or "").strip()
            if not target_id:
                continue
            conn.execute(
                """
                INSERT INTO watch_radar
                    (report_date, target_id, name, type, status, summary, action, confidence,
                     match_count, item_hash, item_title, source, url, score, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_date,
                    target_id[:80],
                    str(row.get("name") or "")[:120],
                    str(row.get("type") or "topic")[:40],
                    str(row.get("status") or "quiet")[:20],
                    str(row.get("summary") or "")[:500],
                    str(row.get("action") or "持续观察")[:20],
                    float(row.get("confidence") or 0),
                    int(row.get("match_count") or 0),
                    str(row.get("item_hash") or ""),
                    str(row.get("item_title") or "")[:300],
                    str(row.get("source") or "")[:120],
                    str(row.get("url") or ""),
                    float(row.get("score") or 0),
                    now,
                ),
            )


def mark_item(
    path: Path,
    item_hash_value: str,
    favorite: int | None = None,
    ignored: int | None = None,
    read_status: str | None = None,
) -> None:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        existing = conn.execute(
            "SELECT favorite, ignored, read_status, read_at FROM user_marks WHERE item_hash = ?",
            (item_hash_value,),
        ).fetchone()
        current_favorite = int(existing[0]) if existing else 0
        current_ignored = int(existing[1]) if existing else 0
        current_status = str(existing[2]) if existing and existing[2] else "unread"
        current_read_at = str(existing[3]) if existing and existing[3] else None
        if favorite is not None:
            current_favorite = 1 if favorite else 0
        if ignored is not None:
            current_ignored = 1 if ignored else 0
        if read_status is not None and read_status in READ_STATUSES:
            current_status = read_status
            if current_status == "read" and not current_read_at:
                current_read_at = now
        conn.execute(
            """
            INSERT INTO user_marks (item_hash, favorite, ignored, read_status, read_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_hash) DO UPDATE SET
                favorite=excluded.favorite,
                ignored=excluded.ignored,
                read_status=excluded.read_status,
                read_at=excluded.read_at,
                updated_at=excluded.updated_at
            """,
            (item_hash_value, current_favorite, current_ignored, current_status, current_read_at, now),
        )


def record_user_event(path: Path, item_hash_value: str, event_type: str, value: float = 1) -> None:
    init_db(path)
    if not item_hash_value or not event_type:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        if event_type in {"open", "detail"}:
            existing = conn.execute(
                "SELECT favorite, ignored, read_status, read_at FROM user_marks WHERE item_hash = ?",
                (item_hash_value,),
            ).fetchone()
            favorite = int(existing[0]) if existing else 0
            ignored = int(existing[1]) if existing else 0
            status = str(existing[2]) if existing and existing[2] else "unread"
            read_at = str(existing[3]) if existing and existing[3] else now
            if status != "archived":
                conn.execute(
                    """
                    INSERT INTO user_marks (item_hash, favorite, ignored, read_status, read_at, updated_at)
                    VALUES (?, ?, ?, 'read', ?, ?)
                    ON CONFLICT(item_hash) DO UPDATE SET
                        read_status='read',
                        read_at=COALESCE(user_marks.read_at, excluded.read_at),
                        updated_at=excluded.updated_at
                    """,
                    (item_hash_value, favorite, ignored, read_at, now),
                )
        conn.execute(
            """
            INSERT INTO user_events (item_hash, event_type, value, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (item_hash_value, event_type[:40], float(value), now),
        )


def record_llm_job(
    path: Path,
    report_date: str,
    job_type: str,
    status: str,
    model: str = "",
    item_count: int = 0,
    error: str = "",
) -> None:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO llm_jobs (report_date, job_type, status, model, item_count, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (report_date, job_type, status, model, int(item_count), error[:1000], now),
        )


def load_dashboard_clusters(
    path: Path,
    report_date: str = "",
    category: str = "",
    limit: int = 24,
) -> list[dict[str, object]]:
    init_db(path)
    if not report_date:
        report_date = latest_report_date(path)
    if not report_date:
        return []

    where = ["c.report_date = ?"]
    params: list[object] = [report_date]
    if category:
        where.append("c.category = ?")
        params.append(category)
    params.append(limit)
    sql = f"""
        SELECT
            c.report_date, c.cluster_id, c.title, c.category, c.summary, c.explanation, c.score, c.size,
            (
                SELECT item_hash
                FROM cluster_items ci
                WHERE ci.report_date = c.report_date AND ci.cluster_id = c.cluster_id
                ORDER BY ci.rank_score DESC
                LIMIT 1
            ) AS top_hash,
            (
                SELECT i.url
                FROM cluster_items ci
                JOIN items i ON i.hash = ci.item_hash
                WHERE ci.report_date = c.report_date AND ci.cluster_id = c.cluster_id
                ORDER BY ci.rank_score DESC
                LIMIT 1
            ) AS top_url
        FROM clusters c
        WHERE {' AND '.join(where)}
        ORDER BY c.score DESC
        LIMIT ?
    """
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def load_item_detail(path: Path, item_hash_value: str, report_date: str = "") -> dict[str, object]:
    init_db(path)
    if not report_date:
        report_date = latest_report_date(path)
    if not report_date or not item_hash_value:
        return {}
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                ir.report_date, i.hash, i.source, i.source_id, i.title, i.url, i.published_at, i.summary,
                i.content, i.category, i.score, i.rank_score, i.ai_summary, i.why,
                i.importance, i.bucket, i.tags_json, i.top_reason, i.raw_json,
                ci.cluster_id, COALESCE(c.size, 1) AS cluster_size,
                COALESCE(m.favorite, 0) AS favorite, COALESCE(m.ignored, 0) AS ignored,
                COALESCE(m.read_status, 'unread') AS read_status
            FROM item_runs ir
            JOIN items i ON i.hash = ir.item_hash
            LEFT JOIN user_marks m ON m.item_hash = i.hash
            LEFT JOIN cluster_items ci ON ci.report_date = ir.report_date AND ci.item_hash = i.hash
            LEFT JOIN clusters c ON c.report_date = ci.report_date AND c.cluster_id = ci.cluster_id
            WHERE ir.report_date = ? AND i.hash = ?
            """,
            (report_date, item_hash_value),
        ).fetchone()
        if not row:
            return {}
        related: list[dict[str, object]] = []
        if row["cluster_id"]:
            related_rows = conn.execute(
                """
                SELECT
                    ci.report_date, i.hash, i.source, i.title, i.url, i.published_at, i.summary,
                    i.category, i.score, i.rank_score, i.ai_summary, i.why, i.importance,
                    i.bucket, i.tags_json, i.top_reason,
                    ci.cluster_id, COALESCE(c.size, 1) AS cluster_size,
                    COALESCE(m.favorite, 0) AS favorite, COALESCE(m.ignored, 0) AS ignored,
                    COALESCE(m.read_status, 'unread') AS read_status
                FROM cluster_items ci
                JOIN items i ON i.hash = ci.item_hash
                LEFT JOIN user_marks m ON m.item_hash = i.hash
                LEFT JOIN clusters c ON c.report_date = ci.report_date AND c.cluster_id = ci.cluster_id
                WHERE ci.report_date = ? AND ci.cluster_id = ? AND i.hash <> ?
                ORDER BY ci.rank_score DESC
                LIMIT 8
                """,
                (report_date, row["cluster_id"], item_hash_value),
            ).fetchall()
            related = [row_to_item_dict(related_row) for related_row in related_rows]
    return {"item": row_to_detail_dict(row), "related": related}


def dashboard_trends(path: Path, limit: int = 14) -> list[dict[str, object]]:
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                rr.report_date, rr.raw_total, rr.deduped_total, rr.inserted,
                COUNT(c.cluster_id) AS cluster_count
            FROM report_runs rr
            LEFT JOIN clusters c ON c.report_date = rr.report_date
            GROUP BY rr.report_date, rr.raw_total, rr.deduped_total, rr.inserted
            ORDER BY rr.report_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    return row[key] if key in row.keys() else default


def safe_raw(row: sqlite3.Row) -> dict[str, Any]:
    try:
        raw = json.loads(str(row_value(row, "raw_json", "{}") or "{}"))
    except json.JSONDecodeError:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def build_judgement(row: sqlite3.Row, tags: list[object], raw: dict[str, Any]) -> dict[str, str]:
    source = str(row["source"] or "")
    category = str(row["category"] or "general")
    rank_score = float(row["rank_score"] or 0)
    importance = int(row["importance"] or 0)
    cluster_size = int(row_value(row, "cluster_size", 1) or 1)
    user_reason = str(row["why"] or "").strip()
    top_reason = str(row["top_reason"] or "").strip()
    reason = "" if "翻译" in user_reason else user_reason
    tag_text = "、".join(str(tag) for tag in tags[:3] if str(tag) and str(tag) != category)
    theme = str(raw.get("world_theme") or "").strip()
    feed = str(raw.get("feed") or "").strip()

    if reason:
        recommendation = reason
    elif cluster_size >= 3:
        recommendation = f"同一主线已聚合 {cluster_size} 条相关内容，说明这不是孤立信号，建议优先看脉络。"
    elif source == "github_trending":
        stars_today = raw.get("stars_today") or 0
        total_stars = raw.get("total_stars") or 0
        recommendation = f"进入 GitHub Trending，当日新增约 {stars_today} stars，总计 {total_stars} stars，适合评估是否加入工具箱。"
    elif source == "github_release":
        recommendation = "重点开源项目发布新版，适合检查新能力、破坏性变更和升级成本。"
    elif source == "github":
        stars = raw.get("stargazers_count") or 0
        pushed = str(raw.get("pushed_at") or "")[:10]
        recommendation = f"近期活跃项目，当前约 {stars} stars，最近更新 {pushed or '未知'}，适合做一次初筛。"
    elif source == "arxiv":
        recommendation = f"论文方向{f'与 {tag_text} 相关' if tag_text else '与当前技术关注面接近'}，适合加入阅读队列。"
    elif category == "world_news":
        recommendation = f"属于{theme or '全球时事'}线索，可能影响宏观环境、产业政策或技术公司外部环境。"
    elif source == "hackernews":
        points = int(float(row["score"] or 0))
        comments = raw.get("descendants") or 0
        recommendation = f"Hacker News 社区讨论热度较高，约 {points} points / {comments} comments，适合看技术圈争议点。"
    elif source.startswith("rss:"):
        recommendation = f"来自 {feed or source.removeprefix('rss:')}，适合补充官方博客、研究机构或行业媒体的更新。"
    elif tag_text:
        recommendation = f"匹配你的关注主题：{tag_text}。"
    elif top_reason:
        recommendation = top_reason
    else:
        recommendation = "进入当前重点排序，适合快速判断是否需要打开原文继续看。"

    if source == "github_trending":
        freshness = "今日趋势：新进入 GitHub Trending。"
    elif source == "github_release":
        freshness = "近期版本：项目有新发布，适合检查变更。"
    elif source == "github":
        pushed = str(raw.get("pushed_at") or "")[:10]
        created = str(raw.get("created_at") or "")[:10]
        freshness = f"项目动态：最近更新 {pushed or '未知'}，创建于 {created or '未知'}。"
    elif str(row["published_at"] or "")[:10] == str(row_value(row, "report_date", "")):
        freshness = "今日新出现：发布时间与当前日报一致。"
    else:
        freshness = "近期信号：来自最近采集窗口。"

    impact_map = {
        "ai": "影响范围：技术认知、模型应用与学习路线。",
        "open_source": "影响范围：工具箱、开发效率与技术选型。",
        "technology": "影响范围：行业趋势、产品判断与工作效率。",
        "programming": "影响范围：工程实践、代码质量与开发流程。",
        "world_news": "影响范围：宏观环境、产业变化与风险意识。",
    }
    impact = impact_map.get(category, "影响范围：日常判断、知识更新与后续观察。")

    risks: list[str] = []
    if source == "gdelt":
        risks.append("新闻聚合源，需注意标题党和单一报道视角")
    if source.startswith("rss:"):
        risks.append("RSS 单源内容，建议结合原文判断")
    if cluster_size <= 1:
        risks.append("来源较单一")
    if rank_score < 70 and importance <= 3:
        risks.append("重要性中等，适合快速浏览")
    if not risks:
        risks.append("风险较低，但仍建议打开原文确认关键细节")
    risk = "；".join(risks) + "。"

    if source == "gdelt":
        caveat = "GDELT 是新闻索引，标题可能被二次转写，先看原文来源和发布时间。"
    elif category == "world_news":
        caveat = "国际新闻容易带媒体视角，建议把它当作线索，必要时再找第二来源确认。"
    elif source == "github_trending":
        caveat = "热度不等于成熟，打开原文后优先看维护频率、issue 和许可证。"
    elif source == "github_release":
        caveat = "重点看 breaking changes、迁移说明和是否影响你正在用的依赖。"
    elif source == "github":
        caveat = "新项目可能还不稳定，先看 README、最近提交和 issue 质量。"
    elif source == "arxiv":
        caveat = "论文结论未必经过长期验证，先看任务设定、数据集和实验对比是否扎实。"
    elif source == "hackernews":
        caveat = "社区热度不等于事实，评论区适合看观点分歧，不适合直接当结论。"
    elif source.startswith("rss:"):
        caveat = "单源 RSS 内容，适合作为更新线索，关键判断仍建议打开原文。"
    elif cluster_size <= 1:
        caveat = "暂未形成多源印证，建议先快速扫读。"
    else:
        caveat = "打开原文后优先确认时间、来源和与你当前工作的关联度。"

    return {
        "recommendation": recommendation,
        "freshness": freshness,
        "impact": impact,
        "risk": risk,
        "caveat": caveat,
    }


def alert_for_item(item: dict[str, object]) -> tuple[str, str, str]:
    source = str(item.get("source") or "")
    title = str(item.get("title") or "")
    rank_score = float(item.get("rank_score") or 0)
    importance = int(item.get("importance") or 0)
    cluster_size = int(item.get("cluster_size") or 1)
    tags = [str(tag) for tag in item.get("tags", []) if str(tag)] if isinstance(item.get("tags"), list) else []

    if source == "github_trending" and (rank_score >= 80 or importance >= 4):
        return (
            "github_spike",
            "GitHub 趋势升温",
            f"{title} 进入今日 Trending 且排序较高，适合评估是否加入工具观察清单。",
        )
    if source == "arxiv" and (rank_score >= 78 or importance >= 4):
        return (
            "paper_signal",
            "高相关论文信号",
            f"{title} 与当前技术关注方向相关，适合加入论文阅读队列。",
        )
    if cluster_size >= 3 and rank_score >= 72:
        return (
            "multi_source",
            "多条目主线形成",
            f"{title} 已聚合 {cluster_size} 条相关内容，可能正在形成一个值得持续观察的主题。",
        )
    if tags and rank_score >= 82:
        return (
            "topic_watch",
            "关注主题触发",
            f"{title} 命中 {tags[0]} 等关注主题，并且综合排序较高。",
        )
    return "", "", ""


def row_to_item_dict(row: sqlite3.Row) -> dict[str, object]:
    try:
        tags = json.loads(row["tags_json"] or "[]")
    except json.JSONDecodeError:
        tags = []
    if not isinstance(tags, list):
        tags = []
    raw = safe_raw(row)
    return {
        "hash": row["hash"],
        "source": row["source"],
        "title": row["title"],
        "url": row["url"],
        "published_at": row["published_at"],
        "summary": row["summary"],
        "category": row["category"],
        "score": row["score"],
        "rank_score": row["rank_score"],
        "ai_summary": row["ai_summary"],
        "why": row["why"],
        "importance": row["importance"],
        "bucket": row["bucket"],
        "tags": tags,
        "top_reason": row["top_reason"],
        "cluster_id": row_value(row, "cluster_id", ""),
        "cluster_size": row_value(row, "cluster_size", 1),
        "favorite": bool(row["favorite"]),
        "ignored": bool(row["ignored"]),
        "read_status": row["read_status"],
        "judgement": build_judgement(row, tags, raw),
    }


def row_to_detail_dict(row: sqlite3.Row) -> dict[str, object]:
    item = row_to_item_dict(row)
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except json.JSONDecodeError:
        raw = {}
    item.update(
        {
            "source_id": row["source_id"],
            "content": row["content"],
            "raw": raw if isinstance(raw, dict) else {},
        }
    )
    return item
