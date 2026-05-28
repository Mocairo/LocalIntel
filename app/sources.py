from __future__ import annotations

import os
import time as time_module
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

from app.config import Settings
from app.http import FetchError, fetch_json, fetch_text
from app.models import Item


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def strip_html(value: str) -> str:
    parser = _HTMLStripper()
    parser.feed(value or "")
    return parser.text()


class GitHubTrendingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.repos: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.capture = ""
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        css = attr.get("class", "")
        if tag == "article" and "Box-row" in css:
            self.current = {}
            return
        if self.current is None:
            return
        if tag == "a" and not self.current.get("repo"):
            href = attr.get("href", "")
            if (
                href.count("/") == 2
                and not href.startswith("/sponsors/")
                and not href.endswith("/stargazers")
                and not href.endswith("/forks")
            ):
                self.current["url"] = f"https://github.com{href}"
                self.capture = "repo"
                self.parts = []
                return
        if tag == "p":
            self.capture = "description"
            self.parts = []
            return
        if tag == "span" and attr.get("itemprop") == "programmingLanguage":
            self.capture = "language"
            self.parts = []
            return
        if tag == "span" and "float-sm-right" in css:
            self.capture = "stars_today"
            self.parts = []
            return
        if tag == "a" and attr.get("href", "").endswith("/stargazers") and not self.current.get("total_stars"):
            self.capture = "total_stars"
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "article":
            if self.current.get("repo") and self.current.get("url"):
                self.repos.append(self.current)
            self.current = None
            self.capture = ""
            self.parts = []
            return
        if self.capture == "repo" and tag != "a":
            return
        if self.capture == "description" and tag != "p":
            return
        if self.capture in ("language", "stars_today") and tag != "span":
            return
        if self.capture == "total_stars" and tag != "a":
            return
        if self.capture and tag in ("a", "p", "span"):
            text = " ".join(" ".join(self.parts).split())
            if self.capture == "repo":
                text = text.replace(" / ", "/").replace(" ", "")
            if text:
                self.current[self.capture] = text
            self.capture = ""
            self.parts = []


ProgressCallback = Callable[[dict[str, Any]], None]


def fetch_all(settings: Settings, run_date: date, progress: ProgressCallback | None = None) -> tuple[list[Item], dict[str, Any]]:
    timeout = int(settings.section("app").get("request_timeout_seconds", 20))
    days_back = int(settings.section("app").get("days_back", 1))
    since = run_date - timedelta(days=days_back)
    stats: dict[str, Any] = {"source_counts": {}, "errors": []}
    items: list[Item] = []

    sources = [
        ("hackernews", fetch_hackernews),
        ("github", fetch_github),
        ("arxiv", fetch_arxiv),
        ("gdelt", fetch_gdelt),
        ("rss", fetch_rss),
    ]
    enabled_sources = [(name, fetcher) for name, fetcher in sources if settings.section(name).get("enabled", False)]
    total_sources = max(1, len(enabled_sources))
    for index, (name, fetcher) in enumerate(enabled_sources):
        section = settings.section(name)
        try:
            if progress:
                progress(
                    {
                        "stage": "fetch",
                        "source": name,
                        "status": "running",
                        "percent": 8 + round(index / total_sources * 38),
                        "message": f"正在抓取 {name}",
                    }
                )
            started = time_module.monotonic()
            batch = fetcher(section, timeout, since, run_date)
            items.extend(batch)
            stats["source_counts"][name] = len(batch)
            stats.setdefault("source_health", {})[name] = {
                "status": "ok",
                "count": len(batch),
                "duration_seconds": round(time_module.monotonic() - started, 2),
                "error": "",
            }
            if progress:
                progress(
                    {
                        "stage": "fetch",
                        "source": name,
                        "status": "ok",
                        "count": len(batch),
                        "percent": 8 + round((index + 1) / total_sources * 38),
                        "message": f"{name} 完成，抓到 {len(batch)} 条",
                    }
                )
        except Exception as exc:  # Keep one failing source from killing the daily report.
            stats["source_counts"][name] = 0
            stats["errors"].append(f"{name}: {exc}")
            stats.setdefault("source_health", {})[name] = {
                "status": "error",
                "count": 0,
                "duration_seconds": 0,
                "error": str(exc),
            }
            if progress:
                progress(
                    {
                        "stage": "fetch",
                        "source": name,
                        "status": "error",
                        "count": 0,
                        "percent": 8 + round((index + 1) / total_sources * 38),
                        "message": f"{name} 失败：{exc}",
                    }
                )
    return items, stats


def fetch_hackernews(section: dict[str, Any], timeout: int, since: date, run_date: date) -> list[Item]:
    limit = int(section.get("limit", 25))
    ids = fetch_json("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=timeout)
    if not isinstance(ids, list):
        raise FetchError("Hacker News topstories response was not a list")

    items: list[Item] = []
    for story_id in ids[:limit]:
        try:
            data = fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=timeout)
        except FetchError:
            continue
        if not isinstance(data, dict) or data.get("type") != "story":
            continue
        title = str(data.get("title") or "").strip()
        if not title:
            continue
        url = str(data.get("url") or f"https://news.ycombinator.com/item?id={story_id}")
        published = ""
        if data.get("time"):
            published = datetime.fromtimestamp(int(data["time"]), tz=timezone.utc).isoformat()
        score = float(data.get("score") or 0)
        comments = data.get("descendants") or 0
        items.append(
            Item(
                source="hackernews",
                source_id=str(story_id),
                title=title,
                url=url,
                published_at=published,
                summary=f"{score:.0f} points, {comments} comments",
                category="technology",
                score=score,
                raw=data,
            )
        )
    return items


def fetch_github(section: dict[str, Any], timeout: int, since: date, run_date: date) -> list[Item]:
    limit = int(section.get("limit", 10))
    queries = section.get("queries", [])
    headers: dict[str, str] = {}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list[Item] = []
    if section.get("trending_enabled", True):
        items.extend(fetch_github_trending(section, timeout, run_date))
        items.extend(fetch_github_releases(section, timeout, since, headers))
        return items[: limit + len(section.get("release_repos", []))]

    per_query = max(1, limit)
    for query in queries:
        q = str(query).format(since=since.isoformat(), date=run_date.isoformat())
        sort = "updated" if "pushed:" in q else "stars"
        params = urlencode({"q": q, "sort": sort, "order": "desc", "per_page": per_query})
        try:
            data = fetch_json(f"https://api.github.com/search/repositories?{params}", timeout=timeout, headers=headers)
        except FetchError:
            continue
        if not isinstance(data, dict):
            continue
        for repo in data.get("items", [])[:per_query]:
            name = str(repo.get("full_name") or "").strip()
            if not name:
                continue
            stars = float(repo.get("stargazers_count") or 0)
            desc = str(repo.get("description") or "").strip()
            lang = repo.get("language") or "unknown"
            created = str(repo.get("created_at") or "")
            pushed = str(repo.get("pushed_at") or "")
            items.append(
                Item(
                    source="github",
                    source_id=name,
                    title=name,
                    url=str(repo.get("html_url") or ""),
                    published_at=created,
                    summary=f"{desc} Language: {lang}. Stars: {stars:.0f}. Updated: {pushed[:10]}.",
                    category="open_source",
                    score=stars,
                    raw=repo,
                )
            )
    items.extend(fetch_github_releases(section, timeout, since, headers))
    return items[: limit * max(1, len(queries)) + len(section.get("release_repos", []))]


def fetch_github_trending(section: dict[str, Any], timeout: int, run_date: date) -> list[Item]:
    limit = int(section.get("limit", 10))
    since = str(section.get("trending_since", "daily"))
    languages = [str(row).strip() for row in section.get("trending_languages", [""]) if str(row).strip() or row == ""]
    if not languages:
        languages = [""]
    items: list[Item] = []
    seen: set[str] = set()
    for language in languages:
        path = f"/trending/{quote(language)}" if language else "/trending"
        text = fetch_text(f"https://github.com{path}?since={quote(since)}", timeout=timeout)
        parser = GitHubTrendingParser()
        parser.feed(text)
        for repo in parser.repos:
            name = repo.get("repo", "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            stars_today = parse_count(repo.get("stars_today", ""))
            total_stars = parse_count(repo.get("total_stars", ""))
            language_name = repo.get("language", "unknown")
            desc = repo.get("description", "")
            items.append(
                Item(
                    source="github_trending",
                    source_id=name,
                    title=name,
                    url=repo.get("url", f"https://github.com/{name}"),
                    published_at=run_date.isoformat(),
                    summary=f"{desc} Language: {language_name}. Stars today: {stars_today:g}. Total stars: {total_stars:g}.",
                    category="open_source",
                    score=stars_today or total_stars,
                    raw={
                        "repo": name,
                        "language": language_name,
                        "stars_today": stars_today,
                        "total_stars": total_stars,
                        "trending_since": since,
                        "trending_language": language,
                    },
                )
            )
            if len(items) >= limit:
                return items
    return items


def parse_count(value: str) -> float:
    cleaned = value.replace(",", "").replace("+", "").strip()
    number = ""
    for char in cleaned:
        if char.isdigit() or char == ".":
            number += char
        elif number:
            break
    try:
        return float(number or 0)
    except ValueError:
        return 0.0


def fetch_github_releases(section: dict[str, Any], timeout: int, since: date, headers: dict[str, str]) -> list[Item]:
    releases: list[Item] = []
    for repo_name in section.get("release_repos", []):
        repo = str(repo_name).strip()
        if not repo:
            continue
        try:
            data = fetch_json(f"https://api.github.com/repos/{repo}/releases?per_page=3", timeout=timeout, headers=headers)
        except FetchError:
            continue
        if not isinstance(data, list):
            continue
        for release in data:
            published = str(release.get("published_at") or "")
            published_date = published[:10]
            if published_date and published_date < since.isoformat():
                continue
            title = str(release.get("name") or release.get("tag_name") or "").strip()
            if not title:
                continue
            releases.append(
                Item(
                    source="github_release",
                    source_id=f"{repo}:{release.get('id')}",
                    title=f"{repo} released {title}",
                    url=str(release.get("html_url") or f"https://github.com/{repo}/releases"),
                    published_at=published,
                    summary=strip_html(str(release.get("body") or "")).strip(),
                    category="open_source",
                    score=8.0,
                    raw={"repo": repo, "release": release},
                )
            )
    return releases


def fetch_arxiv(section: dict[str, Any], timeout: int, since: date, run_date: date) -> list[Item]:
    categories = [str(cat) for cat in section.get("categories", [])]
    if not categories:
        return []
    limit = int(section.get("limit", 20))
    delay = float(section.get("delay_seconds", 3.2))
    keywords = [str(row).casefold() for row in section.get("keywords", [])]
    category_query = " OR ".join(f"cat:{cat}" for cat in categories)
    search_query = (
        f"({category_query}) AND submittedDate:[{since.strftime('%Y%m%d')}0000 "
        f"TO {run_date.strftime('%Y%m%d')}2359]"
    )
    items = fetch_arxiv_query_with_retry(search_query, limit, timeout, delay)
    if not items and section.get("fallback_latest", True):
        time_module.sleep(delay)
        items = fetch_arxiv_query_with_retry(f"({category_query})", limit, timeout, delay)
    if keywords:
        matched = [
            item
            for item in items
            if any(keyword in f"{item.title} {item.summary}".casefold() for keyword in keywords)
        ]
        if matched:
            return matched
    return items


def fetch_arxiv_query_with_retry(search_query: str, limit: int, timeout: int, delay: float) -> list[Item]:
    try:
        return fetch_arxiv_query(search_query, limit, timeout)
    except FetchError as exc:
        if "HTTP 429" not in str(exc):
            raise
        time_module.sleep(delay * 3)
        try:
            return fetch_arxiv_query(search_query, limit, timeout)
        except FetchError as retry_exc:
            if "HTTP 429" in str(retry_exc):
                return []
            raise


def fetch_arxiv_query(search_query: str, limit: int, timeout: int) -> list[Item]:
    params = urlencode(
        {
            "search_query": search_query,
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    text = fetch_text(f"https://export.arxiv.org/api/query?{params}", timeout=timeout)
    root = ET.fromstring(text)

    items: list[Item] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = " ".join((entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split())
        if not title:
            continue
        entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
        summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").split())
        published = entry.findtext("atom:published", default="", namespaces=ATOM_NS) or ""
        link = entry_id
        for link_node in entry.findall("atom:link", ATOM_NS):
            if link_node.attrib.get("title") == "pdf":
                continue
            if link_node.attrib.get("href"):
                link = link_node.attrib["href"]
                break
        items.append(
            Item(
                source="arxiv",
                source_id=entry_id,
                title=title,
                url=link,
                published_at=published,
                summary=summary,
                category="ai",
                score=10.0,
                raw={"id": entry_id, "published": published},
            )
        )
    return items


def fetch_gdelt(section: dict[str, Any], timeout: int, since: date, run_date: date) -> list[Item]:
    limit = int(section.get("limit", 10))
    delay = float(section.get("delay_seconds", 7.0))
    queries = gdelt_query_plan(section, run_date)
    timespan_days = max(1, int(section.get("world_days_back", (run_date - since).days)))
    items: list[Item] = []
    per_query = max(1, limit)

    for index, (query, theme) in enumerate(queries):
        if index:
            time_module.sleep(delay)
        params = urlencode(
            {
                "query": str(query),
                "mode": "ArtList",
                "format": "json",
                "maxrecords": per_query,
                "sort": "HybridRel",
                "timespan": f"{timespan_days}d",
            }
        )
        url = f"https://api.gdeltproject.org/api/v2/doc/doc?{params}"
        try:
            data = fetch_json(url, timeout=timeout)
        except FetchError as exc:
            if "HTTP 429" not in str(exc):
                raise
            time_module.sleep(delay * 2)
            try:
                data = fetch_json(url, timeout=timeout)
            except FetchError:
                continue
        if not isinstance(data, dict):
            continue
        for article in data.get("articles", [])[:per_query]:
            title = str(article.get("title") or "").strip()
            url = str(article.get("url") or "").strip()
            if not title or not url:
                continue
            domain = article.get("domain") or ""
            country = article.get("sourcecountry") or article.get("sourceCountry") or ""
            seen = normalize_gdelt_date(str(article.get("seendate") or ""))
            items.append(
                Item(
                    source="gdelt",
                    source_id=url,
                    title=title,
                    url=url,
                    published_at=seen,
                    summary=f"{domain} {country}".strip(),
                    category="world_news",
                    score=5.0,
                    raw={**article, "world_theme": theme},
                )
            )
    return items[: limit * max(1, len(queries))]


def gdelt_query_plan(section: dict[str, Any], run_date: date | None = None) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for query in section.get("queries", []):
        text = str(query).strip()
        if text:
            rows.append((text, "全球概览"))
    theme_rows: list[tuple[str, str]] = []
    for entry in section.get("theme_pool", []):
        if not isinstance(entry, dict):
            continue
        query = str(entry.get("query") or "").strip()
        theme = str(entry.get("theme") or "全球时事").strip()
        if query:
            theme_rows.append((query, theme))
    per_run = int(section.get("theme_queries_per_run", len(theme_rows) or 0))
    if theme_rows and per_run > 0 and per_run < len(theme_rows):
        start = (run_date.toordinal() if run_date else 0) % len(theme_rows)
        rows.extend(theme_rows[(start + offset) % len(theme_rows)] for offset in range(per_run))
    else:
        rows.extend(theme_rows)
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for query, theme in rows:
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append((query, theme))
    return result


def normalize_gdelt_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return value


def fetch_rss(section: dict[str, Any], timeout: int, since: date, run_date: date) -> list[Item]:
    feeds = section.get("feeds", [])
    limit = int(section.get("limit_per_feed", 8))
    items: list[Item] = []
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        name = str(feed.get("name") or "rss")
        url = str(feed.get("url") or "")
        category = str(feed.get("category") or "general")
        theme = str(feed.get("theme") or "")
        if not url:
            continue
        try:
            text = fetch_text(url, timeout=timeout)
            items.extend(parse_feed(text, name, url, category, limit, theme))
        except Exception:
            continue
    return items


def parse_feed(text: str, feed_name: str, feed_url: str, category: str, limit: int, theme: str = "") -> list[Item]:
    root = ET.fromstring(text)
    if root.tag.endswith("feed"):
        return parse_atom_feed(root, feed_name, category, limit, theme)
    return parse_rss_feed(root, feed_name, feed_url, category, limit, theme)


def parse_atom_feed(root: ET.Element, feed_name: str, category: str, limit: int, theme: str = "") -> list[Item]:
    items: list[Item] = []
    for entry in root.findall("atom:entry", ATOM_NS)[:limit]:
        title = " ".join((entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split())
        link = ""
        for link_node in entry.findall("atom:link", ATOM_NS):
            if link_node.attrib.get("rel", "alternate") == "alternate" and link_node.attrib.get("href"):
                link = link_node.attrib["href"]
                break
        entry_id = entry.findtext("atom:id", default=link, namespaces=ATOM_NS) or link
        summary = entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or ""
        if not summary:
            summary = entry.findtext("atom:content", default="", namespaces=ATOM_NS) or ""
        published = (
            entry.findtext("atom:published", default="", namespaces=ATOM_NS)
            or entry.findtext("atom:updated", default="", namespaces=ATOM_NS)
            or ""
        )
        if title and link:
            items.append(
                Item(
                    source=f"rss:{feed_name}",
                    source_id=entry_id,
                    title=title,
                    url=link,
                    published_at=published,
                    summary=strip_html(summary),
                    category=category,
                    score=3.0,
                    raw={"feed": feed_name, "world_theme": theme},
                )
            )
    return items


def parse_rss_feed(root: ET.Element, feed_name: str, feed_url: str, category: str, limit: int, theme: str = "") -> list[Item]:
    items: list[Item] = []
    for entry in root.findall("./channel/item")[:limit]:
        title = " ".join((entry.findtext("title") or "").split())
        link = (entry.findtext("link") or "").strip()
        guid = (entry.findtext("guid") or link or title).strip()
        published = (entry.findtext("pubDate") or "").strip()
        summary = strip_html(entry.findtext("description") or "")
        if title and link:
            items.append(
                Item(
                    source=f"rss:{feed_name}",
                    source_id=guid,
                    title=title,
                    url=link,
                    published_at=published,
                    summary=summary,
                    category=category,
                    score=3.0,
                    raw={"feed": feed_name, "feed_url": feed_url, "world_theme": theme},
                )
            )
    return items
