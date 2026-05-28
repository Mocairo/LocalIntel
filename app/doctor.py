from __future__ import annotations

import argparse
from pathlib import Path

from app.config import load_dotenv
from app.http import fetch_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Check network access for Local Intel sources.")
    parser.add_argument("--env", default=".env", help="Path to .env")
    args = parser.parse_args()
    load_dotenv(Path(args.env))

    checks = {
        "hackernews": "https://hacker-news.firebaseio.com/v0/topstories.json",
        "github": "https://api.github.com/rate_limit",
        "arxiv": "https://export.arxiv.org/api/query?search_query=cat:cs.AI&max_results=1",
        "gdelt": "https://api.gdeltproject.org/api/v2/doc/doc?query=technology&mode=ArtList&format=json&maxrecords=1",
    }
    ok = True
    for name, url in checks.items():
        try:
            text = fetch_text(url, timeout=20)
            print(f"OK   {name}: {len(text)} bytes")
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
