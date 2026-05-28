from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FetchError(RuntimeError):
    pass


DEFAULT_HEADERS = {
    "User-Agent": "local-intel/0.1 (+https://local)",
    "Accept": "application/json, text/xml, application/xml, text/html;q=0.9, */*;q=0.8",
}


def fetch_text(url: str, timeout: int = 20, headers: dict[str, str] | None = None) -> str:
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    request = Request(url, headers=merged)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise FetchError(f"HTTP {exc.code} for {url}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            reason = getattr(exc, "reason", exc)
            raise FetchError(f"Network error for {url}: {reason}") from exc
    raise FetchError(f"Network error for {url}")


def fetch_json(url: str, timeout: int = 20, headers: dict[str, str] | None = None) -> object:
    text = fetch_text(url, timeout=timeout, headers=headers)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"Invalid JSON from {url}: {exc}") from exc


def post_json(
    url: str,
    payload: dict[str, object],
    timeout: int = 60,
    headers: dict[str, str] | None = None,
    retries: int = 3,
) -> object:
    merged = dict(DEFAULT_HEADERS)
    merged["Content-Type"] = "application/json"
    if headers:
        merged.update(headers)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers=merged)
    text = ""
    for attempt in range(max(1, retries)):
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                text = response.read().decode(charset, errors="replace")
                break
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise FetchError(f"HTTP {exc.code} for {url}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt < max(1, retries) - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            reason = getattr(exc, "reason", exc)
            raise FetchError(f"Network error for {url}: {reason}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"Invalid JSON from {url}: {exc}") from exc
