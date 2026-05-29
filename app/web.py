from __future__ import annotations

import argparse
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.config import Settings, load_settings
from app.config_store import read_ui_config, update_config
from app.db import (
    dashboard_alerts,
    dashboard_stats,
    dashboard_trends,
    latest_report_date,
    list_report_dates,
    load_dashboard_clusters,
    load_dashboard_items,
    load_watch_radar,
    load_watch_radar_history,
    load_watch_target_detail,
    load_item_detail,
    mark_item,
    record_user_event,
)
from app.pipeline import run_pipeline
from app.status import build_runtime_status
from app.weekly import build_weekly_report


class AppState:
    def __init__(self, config_path: Path, env_path: Path, settings: Settings, web_host: str, web_port: int) -> None:
        self.config_path = config_path
        self.env_path = env_path
        self.settings = settings
        self.web_host = web_host
        self.web_port = web_port
        self.db_path = settings.app_path("data_dir") / "intel.sqlite"
        self.running = False
        self.last_result: dict[str, object] = {}
        self.progress: dict[str, object] = {"stage": "idle", "message": "等待重跑", "percent": 0, "sources": {}}
        self.lock = threading.Lock()


class LocalIntelHandler(BaseHTTPRequestHandler):
    state: AppState

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(DASHBOARD_HTML)
            return
        if parsed.path.startswith("/reports/"):
            self.send_report_file(unquote(parsed.path.rsplit("/", 1)[-1]))
            return
        if parsed.path == "/api/dates":
            self.send_json({"dates": list_report_dates(self.state.db_path), "latest": latest_report_date(self.state.db_path)})
            return
        if parsed.path == "/api/items":
            params = parse_qs(parsed.query)
            items = load_dashboard_items(
                self.state.db_path,
                report_date=first(params, "date"),
                category=first(params, "category"),
                bucket=first(params, "bucket"),
                read_status=first(params, "read_status"),
                query=first(params, "q"),
                favorite=first(params, "favorite") == "1",
                include_ignored=first(params, "include_ignored") == "1",
            )
            self.send_json({"items": items})
            return
        if parsed.path == "/api/clusters":
            params = parse_qs(parsed.query)
            clusters = load_dashboard_clusters(
                self.state.db_path,
                report_date=first(params, "date"),
                category=first(params, "category"),
            )
            self.send_json({"clusters": clusters})
            return
        if parsed.path == "/api/runtime-status":
            self.send_json(
                build_runtime_status(self.state.settings, web_host=self.state.web_host, web_port=self.state.web_port)
            )
            return
        if parsed.path == "/api/item":
            params = parse_qs(parsed.query)
            detail = load_item_detail(
                self.state.db_path,
                item_hash_value=first(params, "hash"),
                report_date=first(params, "date"),
            )
            if not detail:
                self.send_error(HTTPStatus.NOT_FOUND, "item not found")
                return
            self.send_json(detail)
            return
        if parsed.path == "/api/trends":
            self.send_json({"trends": dashboard_trends(self.state.db_path)})
            return
        if parsed.path == "/api/alerts":
            params = parse_qs(parsed.query)
            self.send_json({"alerts": dashboard_alerts(self.state.db_path, first(params, "date"))})
            return
        if parsed.path == "/api/watch-radar":
            params = parse_qs(parsed.query)
            self.send_json({"watch_radar": load_watch_radar(self.state.db_path, first(params, "date"))})
            return
        if parsed.path == "/api/watch-radar-history":
            params = parse_qs(parsed.query)
            self.send_json({"watch_radar_history": load_watch_radar_history(self.state.db_path, first(params, "days") or 7)})
            return
        if parsed.path == "/api/watch-target":
            params = parse_qs(parsed.query)
            detail = load_watch_target_detail(self.state.db_path, first(params, "target"), first(params, "days") or 7)
            if not detail:
                self.send_error(HTTPStatus.NOT_FOUND, "watch target not found")
                return
            self.send_json(detail)
            return
        if parsed.path == "/api/weekly":
            params = parse_qs(parsed.query)
            weekly = build_weekly_report(
                self.state.db_path,
                self.state.settings.app_path("report_dir"),
                first(params, "date"),
            )
            self.send_json({"weekly": weekly})
            return
        if parsed.path == "/api/stats":
            params = parse_qs(parsed.query)
            report_date = first(params, "date")
            self.send_json(
                dashboard_stats(self.state.db_path, report_date)
                | {
                    "running": self.state.running,
                    "progress": self.state.progress,
                    "watch_radar": load_watch_radar(self.state.db_path, report_date),
                    "watch_radar_history": load_watch_radar_history(self.state.db_path),
                }
            )
            return
        if parsed.path == "/api/config":
            self.send_json(read_ui_config(self.state.settings))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/mark":
            data = self.read_json()
            item_hash = str(data.get("hash") or "")
            if not item_hash:
                self.send_error(HTTPStatus.BAD_REQUEST, "missing hash")
                return
            favorite = data.get("favorite")
            ignored = data.get("ignored")
            read_status = data.get("read_status")
            mark_item(
                self.state.db_path,
                item_hash,
                favorite=None if favorite is None else int(bool(favorite)),
                ignored=None if ignored is None else int(bool(ignored)),
                read_status=None if read_status is None else str(read_status),
            )
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/event":
            data = self.read_json()
            item_hash = str(data.get("hash") or "")
            event_type = str(data.get("type") or "")
            if not item_hash or not event_type:
                self.send_error(HTTPStatus.BAD_REQUEST, "missing hash/type")
                return
            record_user_event(self.state.db_path, item_hash, event_type)
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/run":
            self.start_pipeline()
            self.send_json({"ok": True, "running": self.state.running})
            return
        if parsed.path == "/api/config":
            data = self.read_json()
            self.state.settings = update_config(self.state.config_path, self.state.env_path, data)
            self.send_json({"ok": True, "config": read_ui_config(self.state.settings)})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def start_pipeline(self) -> None:
        with self.state.lock:
            if self.state.running:
                return
            self.state.running = True
            self.state.progress = {
                "stage": "queued",
                "message": "已加入重跑队列",
                "percent": 1,
                "status": "running",
                "sources": {},
            }

        def progress_callback(event: dict[str, object]) -> None:
            with self.state.lock:
                current = dict(self.state.progress)
                sources = dict(current.get("sources", {})) if isinstance(current.get("sources"), dict) else {}
                source = str(event.get("source") or "")
                if source:
                    sources[source] = {
                        "status": str(event.get("status") or "running"),
                        "count": int(event.get("count") or sources.get(source, {}).get("count", 0) or 0)
                        if isinstance(sources.get(source, {}), dict)
                        else int(event.get("count") or 0),
                        "message": str(event.get("message") or ""),
                    }
                current.update(event)
                current["sources"] = sources
                self.state.progress = current

        def worker() -> None:
            try:
                result = run_pipeline(self.state.config_path, self.state.env_path, "today", progress_callback)
                self.state.last_result = result
            except Exception as exc:
                self.state.last_result = {"error": str(exc)}
                progress_callback({"stage": "error", "message": f"重跑失败：{exc}", "percent": 100, "status": "error"})
            finally:
                with self.state.lock:
                    self.state.running = False

        threading.Thread(target=worker, daemon=True).start()

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_report_file(self, filename: str) -> None:
        report_dir = self.state.settings.app_path("report_dir").resolve()
        target = (report_dir / Path(filename).name).resolve()
        if target.parent != report_dir or target.suffix.lower() not in {".html", ".md"} or not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = "text/html; charset=utf-8" if target.suffix.lower() == ".html" else "text/markdown; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key, [])
    return values[0] if values else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Local Intel dashboard.")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--env", default=".env", help="Path to .env")
    parser.add_argument("--host", default="", help="Override host")
    parser.add_argument("--port", type=int, default=0, help="Override port")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = config_path.parent / env_path
    settings = load_settings(config_path, env_path)
    web = settings.section("web")
    host = args.host or str(web.get("host", "127.0.0.1"))
    port = args.port or int(web.get("port", 8765))

    LocalIntelHandler.state = AppState(config_path, env_path, settings, host, port)
    server = ThreadingHTTPServer((host, port), LocalIntelHandler)
    print(f"Local Intel dashboard: http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>知微情报中枢</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%23f8fbfb'/%3E%3Cpath d='M16 10h25l13 13v31H16z' rx='10' fill='%230f8f8a'/%3E%3Cpath d='M41 10v10c0 3 2 5 5 5h8z' fill='%236ee7db'/%3E%3Cpath d='M24 22v17c0 6 4 9 10 9h11V30' fill='none' stroke='white' stroke-width='6' stroke-linecap='round'/%3E%3Cpath d='M31 31h8M31 38h8' stroke='%236ee7db' stroke-width='3' stroke-linecap='round'/%3E%3Crect x='43' y='27' width='7' height='7' rx='1.5' fill='%236ee7db'/%3E%3C/svg%3E">
  <script>
    (() => {
      try {
        document.documentElement.dataset.theme = localStorage.getItem("localIntelTheme") || "focus";
      } catch {
        document.documentElement.dataset.theme = "focus";
      }
    })();
  </script>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f5;
      --panel: #ffffff;
      --ink: #172026;
      --muted: #62717d;
      --line: #d7dddc;
      --line-strong: #bac5c2;
      --teal: #0f766e;
      --teal-soft: #dff3ee;
      --blue: #2557a7;
      --blue-soft: #e6edf8;
      --amber: #9a650d;
      --amber-soft: #f4ead7;
      --plum: #6d3f83;
      --red: #b42318;
      --shadow: 0 18px 50px rgba(23, 32, 38, 0.12);
      --shadow-soft: 0 10px 28px rgba(23, 32, 38, 0.07);
      --surface: rgba(255, 255, 255, 0.82);
    }
    html[data-theme="night"] {
      color-scheme: dark;
      --bg: #0f1518;
      --panel: #172026;
      --ink: #eef5f4;
      --muted: #9fb0b8;
      --line: #2c3a40;
      --line-strong: #4b5b62;
      --teal: #4bd6c4;
      --teal-soft: rgba(75, 214, 196, 0.16);
      --blue: #8ab8ff;
      --blue-soft: rgba(138, 184, 255, 0.16);
      --amber: #f0c36f;
      --amber-soft: rgba(240, 195, 111, 0.16);
      --plum: #d1a7ef;
      --red: #ff8b80;
      --shadow: 0 18px 50px rgba(0, 0, 0, 0.38);
      --shadow-soft: 0 10px 28px rgba(0, 0, 0, 0.26);
      --surface: rgba(23, 32, 38, 0.86);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(135deg, rgba(15, 118, 110, 0.10), rgba(37, 87, 167, 0.08) 38%, rgba(154, 101, 13, 0.06) 72%, transparent),
        linear-gradient(180deg, #eef3f2 0, #f4f6f5 320px),
        var(--bg);
      color: var(--ink);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.5;
    }
    html[data-theme="night"] body {
      background:
        linear-gradient(135deg, rgba(75, 214, 196, 0.10), rgba(138, 184, 255, 0.07) 42%, rgba(240, 195, 111, 0.06) 72%, transparent),
        linear-gradient(180deg, #131c20 0, #0f1518 340px),
        var(--bg);
    }
    button, input, select, textarea { font: inherit; }
    button, select, input {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--panel);
      color: var(--ink);
    }
    button {
      padding: 0 12px;
      cursor: pointer;
      white-space: nowrap;
      transition: background 150ms ease, border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
    }
    button:hover {
      border-color: var(--line-strong);
      box-shadow: 0 6px 18px rgba(23, 32, 38, 0.08);
      transform: translateY(-1px);
    }
    button.primary { color: #fff; background: linear-gradient(135deg, var(--teal), #0c5f8f); border-color: var(--teal); }
    button.dark { color: #fff; background: linear-gradient(135deg, #172026, #263844); border-color: #172026; }
    button.soft.active, .category-btn.active { color: var(--teal); border-color: var(--teal); background: var(--teal-soft); }
    input, select { padding: 0 10px; }
    textarea {
      width: 100%;
      min-height: 92px;
      padding: 9px 10px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--panel);
      color: var(--ink);
    }
    .app-shell {
      max-width: 1360px;
      margin: 0 auto;
      padding: 0 22px 38px;
    }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 20;
      border-bottom: 1px solid rgba(186, 197, 194, 0.64);
      background: rgba(249, 250, 248, 0.88);
      backdrop-filter: blur(12px);
      box-shadow: 0 8px 30px rgba(23, 32, 38, 0.05);
    }
    .topbar-inner {
      max-width: 1360px;
      margin: 0 auto;
      padding: 18px 22px;
      display: grid;
      grid-template-columns: minmax(260px, 1fr) auto;
      gap: 16px;
      align-items: center;
    }
    .eyebrow {
      color: var(--teal);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    h1 {
      margin: 2px 0;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: 0;
    }
    h2, h3 { letter-spacing: 0; }
    .subtitle {
      color: var(--muted);
      font-size: 13px;
      min-height: 20px;
    }
    .top-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }
    .theme-switch {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      height: 36px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .theme-switch button {
      height: 28px;
      padding: 0 9px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      font-size: 13px;
    }
    .theme-switch button:hover {
      box-shadow: none;
      transform: none;
    }
    .theme-switch button.active {
      color: var(--teal);
      background: var(--panel);
      box-shadow: 0 4px 12px rgba(23, 32, 38, 0.08);
    }
    .command {
      margin: 20px 0 16px;
      padding: 14px;
      display: grid;
      grid-template-columns: 180px 190px minmax(220px, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      border: 1px solid rgba(215, 221, 220, 0.9);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow-soft);
    }
    .command input { width: 100%; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }
    .metric {
      position: relative;
      overflow: hidden;
      min-height: 92px;
      padding: 15px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow-soft);
    }
    .metric::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--teal);
    }
    .metric::after {
      content: "";
      position: absolute;
      right: 0;
      top: 0;
      width: 42%;
      height: 100%;
      clip-path: polygon(28% 0, 100% 0, 100% 100%, 0 100%);
      background: rgba(15, 118, 110, 0.07);
    }
    .metric strong {
      position: relative;
      z-index: 1;
      display: block;
      font-size: 28px;
      line-height: 1.1;
    }
    .metric span {
      position: relative;
      z-index: 1;
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .metric:nth-child(2)::before { background: var(--blue); }
    .metric:nth-child(2)::after { background: rgba(37, 87, 167, 0.10); }
    .metric:nth-child(2) strong { color: var(--blue); }
    .metric:nth-child(3)::before { background: var(--amber); }
    .metric:nth-child(3)::after { background: rgba(154, 101, 13, 0.12); }
    .metric:nth-child(3) strong { color: var(--amber); }
    .metric:nth-child(4)::before { background: var(--plum); }
    .metric:nth-child(4)::after { background: rgba(109, 63, 131, 0.11); }
    .metric:nth-child(4) strong { color: var(--plum); }
    .metric:nth-child(5)::before { background: var(--teal); }
    .metric:nth-child(5) strong { color: var(--teal); }
    .llm-panel {
      display: none;
      margin: 0 0 18px;
      padding: 17px 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: linear-gradient(135deg, #ffffff 0, #f7fbfa 62%, #eef6f4 100%);
      box-shadow: var(--shadow-soft);
    }
    html[data-theme="night"] .llm-panel {
      background: linear-gradient(135deg, #182329 0, #172026 62%, #13272b 100%);
    }
    .llm-panel.show { display: block; }
    .llm-panel h2 {
      margin: 0 0 8px;
      font-size: 17px;
      color: var(--teal);
    }
    .llm-panel pre {
      margin: 0;
      color: var(--ink);
      white-space: pre-wrap;
      font-family: inherit;
      font-size: 14px;
    }
    .llm-panel .llm-muted {
      color: var(--muted);
      font-size: 13px;
    }
    .progress-panel {
      display: none;
      margin: 0 0 18px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    .runtime-panel {
      margin: 0 0 18px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow-soft);
    }
    .runtime-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .runtime-head h2 {
      margin: 0;
      font-size: 16px;
    }
    .runtime-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
    }
    .runtime-card {
      min-width: 0;
      padding: 10px 11px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--panel);
    }
    .runtime-card b {
      display: block;
      font-size: 13px;
      color: var(--muted);
      font-weight: 600;
    }
    .runtime-card span {
      display: block;
      margin-top: 5px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 14px;
      font-weight: 700;
    }
    .runtime-ok { color: var(--teal); }
    .runtime-warn { color: var(--amber); }
    .runtime-error { color: var(--red); }
    .progress-panel.show { display: block; }
    .progress-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }
    .progress-message {
      color: var(--ink);
      font-weight: 600;
    }
    .progress-track {
      height: 8px;
      overflow: hidden;
      border-radius: 999px;
      background: #edf0ef;
    }
    html[data-theme="night"] .progress-track { background: #263238; }
    .progress-fill {
      height: 100%;
      width: 0;
      background: linear-gradient(90deg, var(--teal), var(--blue));
      transition: width 220ms ease;
    }
    .source-progress {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }
    .source-progress span {
      padding: 3px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: #fbfcfb;
      font-size: 12px;
    }
    html[data-theme="night"] .source-progress span { background: #131c20; }
    .source-progress span.ok { color: var(--teal); border-color: #acd8cf; }
    .source-progress span.error { color: var(--red); border-color: #efb0a8; }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: end;
      margin: 10px 0 12px;
    }
    .section-head h2 {
      margin: 0;
      font-size: 20px;
    }
    .section-note {
      color: var(--muted);
      font-size: 13px;
    }
    .cluster-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(210px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }
    .cluster-card {
      position: relative;
      overflow: hidden;
      min-height: 154px;
      padding: 16px;
      border: 1px solid var(--line);
      border-top: 3px solid var(--teal);
      border-radius: 8px;
      background: var(--panel);
      display: grid;
      gap: 8px;
      align-content: start;
      box-shadow: var(--shadow-soft);
    }
    .cluster-card::before {
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 36px;
      background: linear-gradient(90deg, rgba(15, 118, 110, 0.08), rgba(37, 87, 167, 0.04));
      pointer-events: none;
    }
    .cluster-card[data-category="ai"] { border-top-color: var(--plum); }
    .cluster-card[data-category="open_source"] { border-top-color: var(--blue); }
    .cluster-card[data-category="world_news"] { border-top-color: var(--amber); }
    .cluster-card[data-category="technology"] { border-top-color: var(--teal); }
    .cluster-card[data-category="programming"] { border-top-color: #52616b; }
    .cluster-card button {
      width: fit-content;
      height: 32px;
      background: #fbfcfb;
    }
    html[data-theme="night"] .cluster-card button { background: #131c20; }
    .cluster-kicker {
      position: relative;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    .cluster-card h3 {
      position: relative;
      margin: 0;
      font-size: 16px;
      line-height: 1.35;
    }
    .cluster-card h3 a, .intel-card h3 a, .drawer-head h2 a {
      color: var(--ink);
      text-decoration: none;
    }
    .cluster-card h3 a:hover, .intel-card h3 a:hover, .drawer-head h2 a:hover {
      color: var(--teal);
      text-decoration: underline;
      text-underline-offset: 3px;
    }
    .cluster-card p {
      position: relative;
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }
    .cluster-heat {
      position: relative;
      width: fit-content;
      padding: 3px 9px;
      border-radius: 999px;
      color: var(--teal);
      background: var(--teal-soft);
      font-size: 12px;
      font-weight: 700;
    }
    .workspace {
      display: grid;
      grid-template-columns: 276px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .rail {
      position: sticky;
      top: 94px;
      display: grid;
      gap: 14px;
    }
    .rail-section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.88);
      padding: 12px;
      box-shadow: var(--shadow-soft);
    }
    .rail-section h2 {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 10px;
      font-size: 15px;
    }
    .rail-section h2::before {
      content: "";
      width: 4px;
      height: 16px;
      border-radius: 999px;
      background: var(--teal);
    }
    [data-views][hidden] {
      display: none !important;
    }
    .view-nav {
      display: grid;
      gap: 8px;
    }
    .view-nav-btn {
      width: 100%;
      height: auto;
      display: grid;
      grid-template-columns: 22px minmax(0, 1fr);
      gap: 8px 10px;
      align-items: center;
      padding: 10px;
      text-align: left;
      white-space: normal;
      background: #fbfcfb;
      border-color: transparent;
      box-shadow: none;
    }
    html[data-theme="night"] .view-nav-btn { background: #131c20; }
    .view-nav-btn span[data-icon] {
      grid-row: span 2;
      width: 18px;
      height: 18px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: currentColor;
    }
    .view-nav-btn svg {
      width: 18px;
      height: 18px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .view-nav-btn b {
      color: var(--ink);
      font-size: 14px;
      line-height: 1.2;
    }
    .view-nav-btn small {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.2;
    }
    .view-nav-btn.active {
      color: #fff;
      border-color: var(--teal);
      background: linear-gradient(135deg, var(--teal), #0c5f8f);
      box-shadow: 0 10px 24px rgba(15, 118, 110, 0.16);
    }
    .view-nav-btn.active b,
    .view-nav-btn.active small {
      color: #fff;
    }
    .category-list {
      display: grid;
      gap: 7px;
    }
    .category-btn {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: center;
      text-align: left;
      background: #fbfcfb;
      border-color: transparent;
    }
    html[data-theme="night"] .category-btn { background: #131c20; }
    .category-btn span:last-child {
      color: var(--muted);
      font-size: 12px;
    }
    .health, .trend, .weekly {
      display: grid;
      gap: 8px;
    }
    .health-row, .trend-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .health-row > span:first-child {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .health-row i {
      width: 6px;
      height: 6px;
      flex: 0 0 auto;
      border-radius: 50%;
      background: var(--teal);
    }
    .health-row b, .trend-row b {
      color: var(--ink);
      overflow-wrap: anywhere;
    }
    .trend-card {
      display: grid;
      gap: 8px;
    }
    .trend-top {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      color: var(--muted);
      font-size: 12px;
    }
    .trend-top b {
      color: var(--ink);
      font-size: 20px;
    }
    .trend-delta {
      color: var(--teal);
      font-size: 13px;
    }
    .trend-chart {
      width: 100%;
      height: 78px;
      border-radius: 8px;
      background: linear-gradient(180deg, rgba(15, 118, 110, 0.08), rgba(15, 118, 110, 0.02));
    }
    .trend-chart polyline {
      fill: none;
      stroke: var(--teal);
      stroke-width: 3;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .trend-scale {
      display: flex;
      justify-content: space-between;
      color: var(--muted);
      font-size: 11px;
    }
    .weekly-card {
      display: grid;
      gap: 9px;
      padding: 12px;
      border: 1px solid rgba(15, 118, 110, 0.24);
      border-radius: 8px;
      background: linear-gradient(135deg, #f8fbfa, #edf6f4);
    }
    html[data-theme="night"] .weekly-card {
      background: linear-gradient(135deg, #152126, #13292c);
    }
    .weekly-card a {
      color: var(--ink);
      text-decoration: none;
      font-weight: 700;
      line-height: 1.35;
    }
    .weekly-card a:hover {
      color: var(--teal);
      text-decoration: underline;
      text-underline-offset: 3px;
    }
    .weekly-meta {
      color: var(--muted);
      font-size: 12px;
    }
    .weekly-stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
    }
    .weekly-stats span {
      padding: 7px;
      border: 1px solid rgba(15, 118, 110, 0.14);
      border-radius: 7px;
      background: var(--panel);
      color: var(--muted);
      font-size: 12px;
    }
    .weekly-stats b {
      display: block;
      color: var(--ink);
      font-size: 17px;
      line-height: 1.1;
    }
    .weekly-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .weekly-tags span {
      padding: 2px 7px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: var(--panel);
      font-size: 12px;
    }
    .health-ok { color: var(--teal); }
    .health-error { color: var(--red); }
    .barline {
      grid-column: 1 / -1;
      height: 6px;
      overflow: hidden;
      border-radius: 999px;
      background: #edf0ef;
    }
    .barline span {
      display: block;
      height: 100%;
      background: linear-gradient(90deg, var(--teal), var(--blue));
    }
    .feed-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.86);
      box-shadow: var(--shadow-soft);
    }
    .feed-head h2 {
      margin: 0;
      font-size: 21px;
    }
    .feed-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .bucket-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 10px;
    }
    .read-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 14px;
    }
    .bucket-tabs button, .read-tabs button {
      background: rgba(255, 255, 255, 0.82);
    }
    html[data-theme="night"] .bucket-tabs button,
    html[data-theme="night"] .read-tabs button {
      background: rgba(23, 32, 38, 0.86);
    }
    .bucket-tabs button.active, .read-tabs button.active {
      color: var(--teal);
      border-color: var(--teal);
      background: var(--teal-soft);
      box-shadow: 0 8px 20px rgba(15, 118, 110, 0.10);
    }
    .feed-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 12px;
    }
    .intel-card {
      position: relative;
      overflow: hidden;
      min-height: 278px;
      padding: 16px;
      border: 1px solid var(--line);
      border-top: 3px solid var(--blue);
      border-radius: 8px;
      background: var(--panel);
      display: grid;
      grid-template-rows: auto auto 1fr auto auto;
      gap: 10px;
      box-shadow: 0 8px 22px rgba(23, 32, 38, 0.055);
      transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
    }
    .intel-card[data-bucket="must"] { border-top-color: var(--amber); }
    .intel-card[data-bucket="archive"] { border-top-color: var(--muted); }
    .intel-card[data-category="ai"] { background: linear-gradient(180deg, #ffffff 0, #fbf8fd 100%); }
    .intel-card[data-category="open_source"] { background: linear-gradient(180deg, #ffffff 0, #f8fbff 100%); }
    .intel-card[data-category="world_news"] { background: linear-gradient(180deg, #ffffff 0, #fffaf2 100%); }
    .intel-card[data-category="technology"] { background: linear-gradient(180deg, #ffffff 0, #f6fbfa 100%); }
    .intel-card[data-category="programming"] { background: linear-gradient(180deg, #ffffff 0, #f8fafc 100%); }
    html[data-theme="night"] .intel-card[data-category] {
      background: linear-gradient(180deg, #172026 0, #121b20 100%);
    }
    .intel-card::before {
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 34px;
      background: linear-gradient(90deg, rgba(37, 87, 167, 0.07), transparent);
      pointer-events: none;
    }
    .intel-card:hover {
      border-color: var(--line-strong);
      box-shadow: 0 16px 38px rgba(23, 32, 38, 0.11);
      transform: translateY(-2px);
    }
    .card-top {
      position: relative;
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: start;
    }
    .source-pill {
      max-width: 54%;
      padding: 3px 8px;
      border-radius: 999px;
      color: var(--teal);
      background: var(--teal-soft);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .bucket-pill {
      padding: 3px 8px;
      border-radius: 999px;
      color: var(--blue);
      background: var(--blue-soft);
      font-size: 12px;
      white-space: nowrap;
    }
    .bucket-pill.archive {
      color: var(--muted);
      background: #edf0ef;
    }
    .bucket-pill.must {
      color: var(--amber);
      background: var(--amber-soft);
    }
    .read-pill {
      padding: 3px 8px;
      border-radius: 999px;
      color: var(--muted);
      background: #edf0ef;
      font-size: 12px;
      white-space: nowrap;
    }
    .read-pill.unread {
      color: var(--teal);
      background: var(--teal-soft);
    }
    .read-pill.later {
      color: var(--plum);
      background: #eee4f3;
    }
    .read-pill.archived {
      color: var(--red);
      background: #f8dedb;
    }
    .score {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .intel-card h3 {
      position: relative;
      margin: 0;
      font-size: 17px;
      line-height: 1.35;
    }
    .intel-card p { margin: 0; }
    .summary {
      position: relative;
      color: var(--ink);
      font-size: 14px;
    }
    .why {
      position: relative;
      color: var(--muted);
      font-size: 13px;
    }
    .tags {
      position: relative;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      min-height: 24px;
    }
    .tags span {
      padding: 2px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: #fbfcfb;
      font-size: 12px;
    }
    html[data-theme="night"] .tags span { background: #131c20; }
    .meta {
      position: relative;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .card-actions {
      position: relative;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .card-actions a, .drawer-section a {
      display: inline-flex;
      align-items: center;
      height: 36px;
      padding: 0 12px;
      border: 1px solid var(--line);
      border-radius: 7px;
      color: var(--ink);
      text-decoration: none;
      background: var(--panel);
      font-size: 14px;
    }
    .card-actions button, .card-actions a {
      height: 32px;
      padding: 0 10px;
      font-size: 13px;
      background: rgba(255, 255, 255, 0.88);
    }
    html[data-theme="night"] .card-actions button,
    html[data-theme="night"] .card-actions a {
      background: #131c20;
    }
    .empty {
      padding: 26px;
      border: 1px dashed var(--line-strong);
      border-radius: 8px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.72);
    }
    html[data-theme="night"] .empty { background: rgba(23, 32, 38, 0.72); }
    .drawer-backdrop {
      position: fixed;
      inset: 0;
      z-index: 30;
      display: none;
      background: rgba(23, 32, 38, 0.24);
    }
    .drawer-backdrop.open { display: block; }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 60;
      display: none;
      max-width: min(420px, calc(100vw - 36px));
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      background: var(--panel);
      box-shadow: var(--shadow);
      font-size: 14px;
    }
    .toast.show { display: block; }
    .drawer {
      position: fixed;
      top: 0;
      right: 0;
      z-index: 40;
      width: min(720px, 100vw);
      height: 100vh;
      transform: translateX(102%);
      transition: transform 160ms ease;
      background: var(--panel);
      border-left: 1px solid var(--line);
      box-shadow: var(--shadow);
      overflow: auto;
    }
    .drawer.open { transform: translateX(0); }
    .drawer-inner { padding: 24px; }
    .drawer-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: start;
      margin-bottom: 16px;
    }
    .drawer-head h2 {
      margin: 0;
      font-size: 22px;
      line-height: 1.35;
    }
    .drawer-section {
      padding: 15px 0;
      border-top: 1px solid var(--line);
    }
    .drawer-section h3 {
      margin: 0 0 8px;
      font-size: 15px;
    }
    .detail-text {
      color: var(--ink);
      white-space: pre-wrap;
    }
    .related-list {
      display: grid;
      gap: 8px;
    }
    .related-list button {
      width: 100%;
      min-height: 42px;
      height: auto;
      padding: 8px 10px;
      text-align: left;
      white-space: normal;
    }
    .config-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .config-section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }
    .config-section-head h3 {
      margin: 0;
      font-size: 15px;
    }
    .watchlist-editor {
      display: grid;
      gap: 10px;
    }
    .watch-target-row {
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr) 118px 42px;
      gap: 8px;
      align-items: end;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .watch-target-keywords {
      grid-column: 1 / span 2;
    }
    .watch-target-description {
      grid-column: 3 / span 2;
    }
    .watch-target-enabled {
      height: 36px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    .watch-target-enabled input {
      width: auto;
      height: auto;
      margin: 0;
    }
    .watch-target-row textarea {
      min-height: 36px;
      height: 36px;
      resize: vertical;
    }
    .watch-target-remove {
      width: 36px;
      min-width: 36px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--red);
    }
    .watch-target-remove span[data-icon], .watch-target-remove svg {
      width: 16px;
      height: 16px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .field {
      display: grid;
      gap: 6px;
    }
    .field label {
      color: var(--muted);
      font-size: 13px;
    }
    .field input, .field select { width: 100%; }
    .switch-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px 14px;
      margin-bottom: 14px;
      color: var(--muted);
      font-size: 14px;
    }
    .switch-row input {
      height: auto;
      width: auto;
      margin-right: 5px;
    }
    html[data-theme="compact"] .topbar-inner {
      padding: 12px 22px;
    }
    html[data-theme="compact"] h1 {
      font-size: 28px;
    }
    html[data-theme="compact"] .command {
      margin: 14px 0 12px;
      padding: 10px;
    }
    html[data-theme="compact"] .metrics {
      gap: 8px;
      margin-bottom: 14px;
    }
    html[data-theme="compact"] .metric {
      min-height: 74px;
      padding: 11px 13px;
    }
    html[data-theme="compact"] .metric strong {
      font-size: 22px;
    }
    html[data-theme="compact"] .llm-panel {
      padding: 12px 14px;
      margin-bottom: 14px;
    }
    html[data-theme="compact"] .cluster-strip {
      gap: 8px;
      margin-bottom: 14px;
    }
    html[data-theme="compact"] .cluster-card {
      min-height: 132px;
      padding: 12px;
      gap: 6px;
    }
    html[data-theme="compact"] .workspace {
      grid-template-columns: 248px minmax(0, 1fr);
      gap: 12px;
    }
    html[data-theme="compact"] .rail {
      gap: 10px;
    }
    html[data-theme="compact"] .rail-section {
      padding: 10px;
    }
    html[data-theme="compact"] .feed-head {
      padding: 10px 12px;
      margin-bottom: 10px;
    }
    html[data-theme="compact"] .feed-grid {
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 8px;
    }
    html[data-theme="compact"] .intel-card {
      min-height: 230px;
      padding: 12px;
      gap: 7px;
    }
    html[data-theme="compact"] .intel-card h3 {
      font-size: 16px;
    }
    html[data-theme="compact"] .summary,
    html[data-theme="compact"] .why {
      font-size: 13px;
    }
    html[data-theme="compact"] .card-actions button,
    html[data-theme="compact"] .card-actions a {
      height: 28px;
      padding: 0 8px;
      font-size: 12px;
    }
    .app-shell {
      max-width: 1820px;
      padding: 22px 28px 36px;
    }
    .topbar-inner {
      max-width: 1820px;
      min-height: 92px;
      grid-template-columns: minmax(520px, 1fr) auto;
    }
    .brand-row {
      display: flex;
      align-items: center;
      gap: 16px;
      min-width: 0;
    }
    .brand-logo {
      width: 46px;
      height: 46px;
      flex: 0 0 auto;
      display: grid;
      place-items: center;
      border-radius: 13px;
      color: #fff;
      background: linear-gradient(145deg, #0f766e, #10a69a);
      box-shadow: 0 14px 30px rgba(15, 118, 110, 0.22);
    }
    .brand-logo svg {
      width: 28px;
      height: 28px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .brand-copy {
      min-width: 190px;
    }
    .brand-copy h1 {
      margin: 2px 0 0;
      font-size: 27px;
      line-height: 1.1;
    }
    .brand-row .subtitle {
      position: relative;
      margin-left: 110px;
      padding-left: 24px;
      color: #53636d;
      font-size: 14px;
      white-space: nowrap;
    }
    .brand-row .subtitle::before {
      content: "";
      position: absolute;
      left: 0;
      top: 50%;
      width: 15px;
      height: 15px;
      transform: translateY(-50%);
      border: 1.8px solid #a3adb4;
      border-radius: 50%;
    }
    .brand-row .subtitle::after {
      content: "";
      position: absolute;
      left: 7px;
      top: calc(50% - 5px);
      width: 4px;
      height: 6px;
      border-left: 1.6px solid #a3adb4;
      border-bottom: 1.6px solid #a3adb4;
    }
    .icon-action {
      min-width: 106px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      height: 44px;
      border-radius: 8px;
      font-weight: 650;
      background: rgba(255, 255, 255, 0.82);
      box-shadow: 0 10px 28px rgba(23, 32, 38, 0.06);
    }
    .icon-action span[data-icon], .view-toggle span[data-icon], .card-actions span[data-icon] {
      width: 16px;
      height: 16px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: currentColor;
    }
    .icon-action svg, .view-toggle svg, .card-actions svg {
      width: 16px;
      height: 16px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .dashboard-layout {
      display: grid;
      grid-template-columns: 270px minmax(0, 1fr);
      gap: 36px;
      align-items: start;
    }
    .main-area {
      min-width: 0;
      display: grid;
      gap: 14px;
    }
    .rail {
      position: sticky;
      top: 112px;
      gap: 12px;
    }
    .rail-section {
      border: 1px solid #e3e8ea;
      background: rgba(255, 255, 255, 0.86);
      box-shadow: 0 16px 38px rgba(23, 32, 38, 0.06);
    }
    .rail-section h2 {
      color: #16242b;
      font-size: 15px;
      font-weight: 750;
    }
    .category-btn {
      height: 30px;
      padding: 0 10px;
      border-radius: 6px;
      background: transparent;
      color: #24333b;
    }
    .category-btn.active {
      background: linear-gradient(90deg, rgba(15, 118, 110, 0.16), rgba(15, 118, 110, 0.06));
      box-shadow: none;
    }
    .command {
      margin: 0;
      padding: 14px 16px;
      grid-template-columns: 206px 214px minmax(280px, 1fr) 82px 82px;
      gap: 16px;
      border-color: #dfe6e8;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: 0 12px 34px rgba(23, 32, 38, 0.055);
    }
    .command button {
      height: 42px;
    }
    .command input, .command select {
      height: 42px;
      border-radius: 8px;
    }
    .metrics {
      grid-template-columns: repeat(5, minmax(160px, 1fr));
      gap: 16px;
      margin: 0;
    }
    .metric {
      min-height: 112px;
      display: grid;
      grid-template-columns: 66px 1fr;
      align-items: center;
      gap: 16px;
      padding: 18px 20px;
      border-color: #e1e7e9;
      box-shadow: 0 16px 38px rgba(23, 32, 38, 0.06);
    }
    .metric::before, .metric::after {
      display: none;
    }
    .metric-icon {
      width: 66px;
      height: 66px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      color: var(--teal);
      background: rgba(15, 118, 110, 0.10);
    }
    .metric-icon svg {
      width: 30px;
      height: 30px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .metric-icon.blue { color: #2365c9; background: rgba(35, 101, 201, 0.10); }
    .metric-icon.amber { color: #f97316; background: rgba(249, 115, 22, 0.12); }
    .metric-icon.plum { color: #7a34c8; background: rgba(122, 52, 200, 0.12); }
    .metric strong {
      font-size: 30px;
      color: #18232b;
    }
    .metric small {
      display: block;
      margin-top: 3px;
      color: var(--muted);
      font-size: 13px;
    }
    .llm-panel {
      position: relative;
      min-height: 138px;
      margin: 0;
      padding: 18px 22px 24px;
      overflow: hidden;
      border-color: #e1e7e9;
      background: rgba(255, 255, 255, 0.92);
      box-shadow: 0 16px 38px rgba(23, 32, 38, 0.06);
    }
    .llm-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }
    .llm-head h2 {
      margin: 0;
      font-size: 19px;
      color: var(--teal);
    }
    .llm-head span {
      color: var(--muted);
      font-size: 12px;
    }
    .llm-panel pre {
      max-width: calc(100% - 150px);
      line-height: 1.7;
    }
    .sparkline {
      position: absolute;
      right: 0;
      bottom: 0;
      width: 190px;
      height: 54px;
      opacity: 0.72;
      background:
        linear-gradient(135deg, transparent 0 46%, rgba(15, 118, 110, 0.26) 47% 53%, transparent 54%),
        linear-gradient(160deg, transparent 0 50%, rgba(15, 118, 110, 0.48) 51% 58%, transparent 59%);
      clip-path: polygon(0 82%, 18% 82%, 28% 70%, 33% 78%, 45% 60%, 50% 68%, 60% 50%, 66% 62%, 78% 48%, 86% 56%, 100% 42%, 100% 100%, 0 100%);
    }
    .section-head {
      margin: 2px 0 8px;
    }
    .section-head h2 {
      font-size: 22px;
    }
    .cluster-strip {
      gap: 16px;
      margin: 0;
    }
    .cluster-card {
      min-height: 174px;
      padding: 20px 22px;
      border-top-width: 4px;
      box-shadow: 0 14px 34px rgba(23, 32, 38, 0.06);
    }
    .cluster-card::before {
      height: 46px;
      background: linear-gradient(90deg, rgba(15, 118, 110, 0.08), transparent);
    }
    .cluster-kicker {
      color: var(--teal);
    }
    .cluster-card h3 {
      font-size: 18px;
      color: #19313b;
    }
    .cluster-card button {
      display: none;
    }
    .feed-panel {
      padding: 14px 16px 16px;
      border: 1px solid #e1e7e9;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.82);
      box-shadow: 0 16px 38px rgba(23, 32, 38, 0.055);
    }
    .feed-head {
      padding: 0;
      margin: 0 0 10px;
      border: 0;
      background: transparent;
      box-shadow: none;
    }
    .feed-head h2 {
      font-size: 20px;
    }
    .filter-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 8px;
      margin-bottom: 12px;
    }
    .bucket-tabs, .read-tabs {
      margin: 0;
    }
    .bucket-tabs button, .read-tabs button {
      height: 36px;
      border-color: transparent;
      border-radius: 8px;
      background: rgba(246, 248, 249, 0.9);
      color: #394850;
    }
    .bucket-tabs button.active, .read-tabs button.active {
      border-color: rgba(15, 118, 110, 0.18);
      background: var(--teal-soft);
    }
    .view-toggle {
      display: inline-flex;
      gap: 4px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .view-toggle button {
      width: 34px;
      height: 32px;
      padding: 0;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
    }
    .view-toggle button.active {
      color: #fff;
      background: var(--teal);
    }
    .feed-grid {
      grid-template-columns: repeat(4, minmax(230px, 1fr));
      gap: 16px;
    }
    .feed-grid.list {
      grid-template-columns: 1fr;
    }
    .feed-grid.list .intel-card {
      min-height: 150px;
      grid-template-rows: auto auto auto auto;
    }
    .intel-card {
      min-height: 282px;
      padding: 18px;
      border-top-width: 0;
      box-shadow: 0 12px 30px rgba(23, 32, 38, 0.055);
    }
    .intel-card::before {
      display: none;
    }
    .card-top {
      align-items: center;
    }
    .source-pill, .bucket-pill, .read-pill, .tags span {
      border: 0;
    }
    .source-pill {
      max-width: 50%;
      background: #e8f7f4;
    }
    .bucket-pill.must {
      background: #fff1d8;
    }
    .read-pill.unread {
      background: #dff4ef;
    }
    .score {
      margin-left: auto;
    }
    .intel-card h3 {
      font-size: 17px;
    }
    .summary {
      line-height: 1.65;
    }
    .card-actions {
      gap: 7px;
      padding-top: 8px;
      border-top: 1px solid #edf1f2;
    }
    .card-actions button, .card-actions a {
      height: 28px;
      padding: 0 4px;
      border: 0;
      background: transparent;
      color: #24333b;
      gap: 4px;
    }
    .theme-field {
      display: grid;
      grid-template-columns: 92px 1fr;
      align-items: center;
      gap: 12px;
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 14px;
    }
    html[data-theme="night"] .topbar,
    html[data-theme="night"] .command,
    html[data-theme="night"] .rail-section,
    html[data-theme="night"] .feed-panel {
      background: rgba(23, 32, 38, 0.86);
      border-color: var(--line);
    }
    html[data-theme="night"] .brand-row .subtitle { color: var(--muted); }
    html[data-theme="night"] .metric,
    html[data-theme="night"] .cluster-card,
    html[data-theme="night"] .llm-panel,
    html[data-theme="night"] .intel-card {
      background: var(--panel);
      border-color: var(--line);
    }
    html[data-theme="night"] .metric strong,
    html[data-theme="night"] .cluster-card h3,
    html[data-theme="night"] .rail-section h2,
    html[data-theme="night"] .card-actions button,
    html[data-theme="night"] .card-actions a {
      color: var(--ink);
    }
    html[data-theme="night"] .source-pill { background: rgba(75, 214, 196, 0.13); }
    html[data-theme="compact"] .dashboard-layout {
      grid-template-columns: 250px minmax(0, 1fr);
      gap: 20px;
    }
    html[data-theme="compact"] .topbar-inner {
      min-height: 74px;
    }
    html[data-theme="compact"] .brand-logo {
      width: 40px;
      height: 40px;
    }
    html[data-theme="compact"] .brand-row .subtitle {
      margin-left: 50px;
    }
    html[data-theme="compact"] .metric {
      min-height: 82px;
      grid-template-columns: 46px 1fr;
    }
    html[data-theme="compact"] .metric-icon {
      width: 46px;
      height: 46px;
    }
    html[data-theme="compact"] .feed-grid {
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    }
    body {
      background: #f6f8fa;
    }
    html[data-theme="night"] body {
      background: var(--bg);
    }
    button.primary {
      background: #0f8f8a;
      border-color: #0f8f8a;
    }
    button.dark {
      background: #172026;
      border-color: #172026;
    }
    .app-shell {
      max-width: 1500px;
      padding: 24px;
    }
    .topbar {
      background: rgba(255, 255, 255, 0.94);
      border-bottom-color: #e7ecef;
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.035);
    }
    .topbar-inner {
      max-width: 1500px;
      min-height: 66px;
      padding: 9px 24px;
      grid-template-columns: minmax(460px, 1fr) auto;
      gap: 18px;
    }
    .brand-row {
      gap: 12px;
    }
    .brand-logo {
      width: 36px;
      height: 36px;
      border-radius: 11px;
      overflow: hidden;
      background: #f8fbfb;
      box-shadow: 0 8px 18px rgba(15, 118, 110, 0.14), inset 0 0 0 1px rgba(15, 118, 110, 0.08);
    }
    .brand-logo svg {
      width: 36px;
      height: 36px;
      fill: none;
      stroke: none;
      stroke-width: 0;
    }
    .brand-copy {
      min-width: 172px;
      display: grid;
      gap: 1px;
    }
    .eyebrow {
      color: #0f766e;
      font-size: 10.5px;
      font-weight: 760;
      line-height: 1;
    }
    .brand-copy h1 {
      font-size: 21px;
      line-height: 1.05;
    }
    .brand-row .subtitle {
      margin-left: 28px;
      padding: 6px 10px 6px 28px;
      border: 1px solid #e5e7eb;
      border-radius: 999px;
      color: #5f6f78;
      background: #f8fbfb;
      font-size: 12px;
      line-height: 1;
    }
    .brand-row .subtitle::before {
      left: 10px;
      width: 13px;
      height: 13px;
      border-width: 1.5px;
      border-color: #9aa7ae;
    }
    .brand-row .subtitle::after {
      left: 16px;
      top: calc(50% - 4px);
      width: 3px;
      height: 5px;
      border-left-width: 1.4px;
      border-bottom-width: 1.4px;
      border-color: #9aa7ae;
    }
    .top-actions {
      gap: 8px;
    }
    .icon-action {
      min-width: 86px;
      height: 36px;
      padding: 0 13px;
      border-radius: 9px;
      background: #fff;
      color: #1f2937;
      font-size: 13px;
      font-weight: 680;
      box-shadow: 0 6px 16px rgba(15, 23, 42, 0.035);
    }
    button.primary.icon-action {
      min-width: 108px;
      background: #0f8f8a;
      border-color: #0f8f8a;
      color: #fff;
      box-shadow: 0 8px 18px rgba(15, 143, 138, 0.18);
    }
    .dashboard-layout {
      grid-template-columns: 250px minmax(0, 1fr);
      gap: 24px;
    }
    .main-area {
      gap: 12px;
    }
    .rail {
      top: 98px;
      gap: 12px;
    }
    .rail-section {
      padding: 12px;
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      background: #fff;
      box-shadow: 0 10px 24px rgba(17, 24, 39, 0.045);
    }
    .rail-section h2 {
      margin: 0 0 10px;
      color: #111827;
      font-size: 13px;
      font-weight: 760;
      line-height: 1.2;
    }
    .rail-section h2::before {
      width: 3px;
      height: 15px;
      background: #0f766e;
    }
    .category-list {
      gap: 4px;
    }
    .category-btn {
      position: relative;
      height: 26px;
      grid-template-columns: 3px minmax(0, 1fr) auto;
      gap: 8px;
      padding: 0 10px;
      border: 1px solid transparent;
      border-radius: 9px;
      background: #fff;
      color: #374151;
      font-size: 12px;
      font-weight: 600;
      transition: background 150ms ease, border-color 150ms ease, color 150ms ease, box-shadow 150ms ease;
    }
    .category-btn::before {
      content: "";
      width: 3px;
      height: 16px;
      border-radius: 999px;
      background: transparent;
    }
    .category-btn:hover {
      border-color: #d8eeeb;
      background: #f4fbfa;
      box-shadow: none;
    }
    .category-btn.active {
      border-color: #0f766e;
      background: #e8f6f4;
      color: #111827;
      box-shadow: 0 6px 14px rgba(15, 118, 110, 0.08);
    }
    .category-btn.active::before {
      background: #0f766e;
    }
    .category-label {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .category-count {
      min-width: 24px;
      color: #6b7280;
      font-size: 12px;
      font-weight: 650;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    .health, .trend, .weekly {
      gap: 0;
    }
    .health-row {
      min-height: 31px;
      align-items: center;
      gap: 10px;
      padding: 4px 0;
      border-bottom: 1px solid #f0f2f4;
      color: #6b7280;
      font-size: 12px;
      line-height: 1.35;
    }
    .health-row:last-child {
      border-bottom: 0;
    }
    .health-source {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .health-source > span {
      display: inline-flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
    }
    .health-source b {
      color: #111827;
      font-size: 12px;
      font-weight: 750;
      line-height: 1.35;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .health-source small {
      font-size: 11px;
      font-weight: 650;
      letter-spacing: 0;
      line-height: 1.25;
    }
    .health-row i {
      width: 6px;
      height: 6px;
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.09);
    }
    .health-count {
      min-width: 30px;
      color: #0f766e;
      font-weight: 750;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    .trend-card {
      gap: 8px;
    }
    .trend-top {
      align-items: flex-start;
    }
    .trend-top div {
      display: grid;
      gap: 3px;
    }
    .trend-top b {
      color: #111827;
      font-size: 23px;
      font-weight: 780;
      line-height: 1;
      letter-spacing: 0;
    }
    .trend-top small {
      color: #0f766e;
      font-size: 11px;
      font-weight: 700;
    }
    .trend-top > span {
      padding: 3px 8px;
      border: 1px solid #e5e7eb;
      border-radius: 999px;
      color: #6b7280;
      background: #f9fbfb;
      font-size: 11px;
      font-weight: 650;
    }
    .trend-chart-shell {
      position: relative;
      height: 48px;
      overflow: hidden;
      padding: 6px 4px 4px;
      border: 1px solid #e9eeee;
      border-radius: 11px;
      background: #f8fbfb;
    }
    .trend-chart-shell::before,
    .trend-chart-shell::after {
      content: "";
      position: absolute;
      left: 8px;
      right: 8px;
      border-top: 1px solid rgba(107, 114, 128, 0.10);
    }
    .trend-chart-shell::before {
      top: 15px;
    }
    .trend-chart-shell::after {
      top: 31px;
    }
    .trend-chart {
      position: relative;
      z-index: 1;
      width: 100%;
      height: 38px;
      background: transparent;
    }
    .trend-chart polyline {
      stroke: #0f766e;
      stroke-width: 3;
    }
    .trend-scale {
      margin-top: 1px;
      color: #6b7280;
      font-size: 10.5px;
    }
    .weekly-card {
      gap: 8px;
      padding: 0;
      border: 0;
      background: transparent;
    }
    .weekly-head {
      display: grid;
      gap: 4px;
      padding-bottom: 8px;
      border-bottom: 1px solid #f0f2f4;
    }
    .weekly-card a {
      color: #111827;
      font-size: 14px;
      font-weight: 780;
    }
    .weekly-meta {
      color: #6b7280;
      font-size: 11.5px;
      line-height: 1.25;
    }
    .weekly-stats {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
    }
    .weekly-stats span {
      min-height: 46px;
      padding: 7px 8px;
      border-color: #e5e7eb;
      border-radius: 10px;
      background: #f9fbfb;
      color: #6b7280;
      font-size: 11px;
      line-height: 1.15;
      text-align: left;
    }
    .weekly-stats b {
      color: #111827;
      font-size: 17px;
      font-weight: 780;
    }
    .weekly-stats em {
      display: block;
      margin-top: 4px;
      font-style: normal;
    }
    .weekly-tags {
      display: none;
    }
    html[data-theme="night"] .rail-section {
      border-color: var(--line);
      background: var(--panel);
      box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
    }
    html[data-theme="night"] .rail-section h2,
    html[data-theme="night"] .category-btn.active,
    html[data-theme="night"] .health-source b,
    html[data-theme="night"] .trend-top b,
    html[data-theme="night"] .weekly-card a,
    html[data-theme="night"] .weekly-stats b {
      color: var(--ink);
    }
    html[data-theme="night"] .category-btn {
      background: transparent;
      color: var(--ink);
    }
    html[data-theme="night"] .category-btn:hover,
    html[data-theme="night"] .category-btn.active,
    html[data-theme="night"] .trend-chart-shell,
    html[data-theme="night"] .trend-top > span,
    html[data-theme="night"] .weekly-stats span {
      border-color: var(--line);
      background: rgba(75, 214, 196, 0.10);
    }
    .command {
      padding: 12px;
      grid-template-columns: 190px 190px minmax(240px, 1fr) 78px 72px;
      gap: 12px;
      border-color: #e3e9ec;
      background: #fff;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.045);
    }
    .command input,
    .command select,
    .command button {
      height: 40px;
    }
    .metrics {
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
    }
    .metric {
      min-height: 88px;
      grid-template-columns: 46px minmax(0, 1fr);
      gap: 11px;
      padding: 12px 14px;
      align-items: center;
      border-color: #e3e9ec;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.045);
    }
    .metric-icon {
      width: 46px;
      height: 46px;
      flex: 0 0 46px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
    }
    .metric-icon svg {
      width: 22px;
      height: 22px;
      display: block;
    }
    .metric strong {
      display: block;
      font-size: 24px;
      line-height: 1.05;
    }
    .metric small {
      margin-top: 2px;
      font-size: 11px;
    }
    .metrics {
      gap: 10px;
    }
    .metric {
      min-height: 74px;
      grid-template-columns: 38px minmax(0, 1fr);
      gap: 10px;
      padding: 10px 13px;
      align-items: center;
    }
    .metric-icon {
      width: 38px;
      height: 38px;
      margin: 0;
      align-self: center;
      justify-self: center;
    }
    .metric-icon svg {
      width: 18px;
      height: 18px;
    }
    .metric-copy {
      min-width: 0;
      display: grid;
      align-content: center;
      gap: 2px;
      line-height: 1.15;
    }
    .metric-copy strong {
      overflow: hidden;
      color: #17202a;
      font-size: 23px;
      line-height: 1;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .metric-label {
      display: block;
      margin: 0;
      color: #3d4b55;
      font-size: 12px;
      line-height: 1.15;
      white-space: nowrap;
    }
    .metric small {
      display: block;
      margin: 0;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.1;
      white-space: nowrap;
    }
    .metric-blue .metric-copy strong { color: #2459b8; }
    .metric-amber .metric-copy strong { color: #9a650d; }
    .metric-plum .metric-copy strong { color: #6d3f83; }
    .metric-teal .metric-copy strong { color: var(--teal); }
    .metrics .metric {
      height: 72px;
      min-height: 72px;
      grid-template-columns: 38px minmax(0, 1fr);
      gap: 10px;
      padding: 9px 12px;
    }
    .metrics .metric .metric-icon {
      width: 38px;
      height: 38px;
      margin: 0;
      display: grid;
      align-self: center;
      justify-self: center;
      place-items: center;
    }
    .metrics .metric .metric-icon svg {
      width: 18px;
      height: 18px;
      display: block;
    }
    .metrics .metric .metric-copy {
      min-width: 0;
      display: grid;
      align-content: center;
      gap: 2px;
      transform: translateY(-1px);
    }
    .metrics .metric .metric-copy strong {
      margin: 0;
      font-size: 22px;
      line-height: 1;
      letter-spacing: 0;
    }
    .metrics .metric .metric-label,
    .metrics .metric small {
      margin: 0;
      line-height: 1.12;
    }
    .llm-panel {
      min-height: 0;
      padding: 14px 18px;
      border-color: #e3e9ec;
      background: #fff;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.045);
    }
    html[data-theme="night"] .llm-panel {
      background: var(--panel);
    }
    .llm-head {
      margin-bottom: 6px;
    }
    .llm-head h2 {
      font-size: 17px;
    }
    .llm-panel pre {
      max-width: calc(100% - 90px);
      line-height: 1.55;
      font-size: 13px;
    }
    .sparkline {
      width: 120px;
      height: 34px;
      opacity: 0.45;
      background: #d9f1ec;
    }
    .alerts-panel {
      display: none;
      padding: 14px 16px;
      border: 1px solid #e3e9ec;
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.045);
    }
    .alerts-panel.show {
      display: block;
    }
    .alerts-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .alerts-head h2 {
      margin: 0;
      color: #111827;
      font-size: 17px;
    }
    .alerts-head span {
      color: #6b7280;
      font-size: 12px;
    }
    .alerts-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .alerts-grid.count-1,
    .alerts-grid.count-2 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .alerts-grid.count-4 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .watch-panel {
      display: none;
      padding: 14px 16px;
      border: 1px solid #e3e9ec;
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.045);
    }
    .watch-panel.show {
      display: block;
    }
    .watch-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .watch-head h2 {
      margin: 0;
      color: #111827;
      font-size: 17px;
    }
    .watch-head span {
      color: #6b7280;
      font-size: 12px;
    }
    .watch-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .watch-grid.count-1,
    .watch-grid.count-2,
    .watch-grid.count-4 {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .watch-card {
      min-height: 112px;
      padding: 12px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #fbfcfc;
    }
    .watch-card strong {
      display: block;
      margin-bottom: 6px;
      color: #0f766e;
      font-size: 13px;
    }
    .watch-card a, .watch-target-link {
      display: block;
      margin: 4px 0 6px;
      color: #111827;
      text-decoration: none;
      font-weight: 750;
      line-height: 1.35;
    }
    .watch-target-link {
      width: 100%;
      height: auto;
      padding: 0;
      border: 0;
      background: transparent;
      box-shadow: none;
      text-align: left;
      white-space: normal;
    }
    .watch-target-link:hover {
      color: #0f766e;
      box-shadow: none;
      transform: none;
    }
    .watch-item-link {
      margin-top: 7px;
      color: #0f766e;
      font-size: 12px;
      font-weight: 650;
    }
    .watch-card p {
      margin: 0;
      color: #374151;
      font-size: 12px;
      line-height: 1.5;
    }
    .watch-history {
      display: none;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid #edf0f2;
    }
    .watch-history.show {
      display: grid;
      gap: 8px;
    }
    .watch-history-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      color: #6b7280;
      font-size: 12px;
    }
    .watch-history-head h3 {
      margin: 0;
      color: #111827;
      font-size: 15px;
    }
    .watch-history-row {
      display: grid;
      grid-template-columns: minmax(120px, 1fr) minmax(180px, 1.4fr) minmax(140px, 0.9fr);
      gap: 10px;
      align-items: center;
      width: 100%;
      height: auto;
      padding: 9px 10px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #fbfcfc;
      color: inherit;
      text-align: left;
      white-space: normal;
    }
    .watch-history-row:hover {
      border-color: #b8d8d4;
      box-shadow: 0 8px 18px rgba(15, 118, 110, 0.08);
      transform: none;
    }
    .watch-history-name {
      display: grid;
      gap: 2px;
      min-width: 0;
    }
    .watch-history-name strong {
      overflow: hidden;
      color: #111827;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 13px;
    }
    .watch-history-name span,
    .watch-history-meta {
      color: #6b7280;
      font-size: 12px;
    }
    .watch-dots {
      display: flex;
      align-items: center;
      gap: 6px;
      min-height: 18px;
    }
    .watch-dot {
      width: 12px;
      height: 12px;
      border-radius: 999px;
      border: 1px solid #cbd5e1;
      background: #eef2f7;
    }
    .watch-dot.active {
      border-color: #0f766e;
      background: #0f8f8a;
      box-shadow: 0 0 0 3px rgba(15, 143, 138, 0.12);
    }
    .watch-history-meta {
      text-align: right;
    }
    .watch-detail-records {
      display: grid;
      gap: 10px;
    }
    .watch-detail-record {
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
    }
    .watch-detail-record b {
      display: block;
      margin-bottom: 5px;
    }
    .watch-detail-record p {
      margin: 6px 0 0;
      color: var(--ink);
      font-size: 13px;
    }
    .watch-detail-record a {
      display: inline-block;
      margin-top: 6px;
    }
    .sources-panel {
      display: grid;
      gap: 12px;
    }
    .sources-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }
    .alert-card {
      min-height: 96px;
      padding: 12px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #f9fbfb;
    }
    .alert-card strong {
      display: block;
      margin-bottom: 6px;
      color: #0f766e;
      font-size: 13px;
    }
    .alert-card a {
      display: block;
      margin-bottom: 6px;
      color: #111827;
      text-decoration: none;
      font-weight: 750;
      line-height: 1.35;
    }
    .alert-card a:hover {
      color: #0f766e;
      text-decoration: underline;
      text-underline-offset: 3px;
    }
    .alert-card p {
      margin: 0;
      color: #6b7280;
      font-size: 12px;
      line-height: 1.45;
    }
    .judgement-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 6px;
    }
    .judgement-row {
      display: block;
      min-height: 0;
      padding: 8px 9px;
      border: 1px solid #edf1f2;
      border-radius: 8px;
      background: #fbfcfc;
      color: #4b5563;
      font-size: 12px;
      line-height: 1.45;
    }
    .judgement-row b {
      display: inline;
      margin: 0 6px 0 0;
      color: #0f766e;
      font-size: 11px;
      font-weight: 760;
      white-space: nowrap;
    }
    .judgement-row span {
      color: #374151;
    }
    .drawer-judgement {
      grid-template-columns: 1fr;
    }
    .drawer-judgement .judgement-row {
      min-height: 0;
    }
    .section-head {
      margin: 4px 0 8px;
    }
    .section-head h2 {
      font-size: 20px;
    }
    .cluster-strip {
      gap: 12px;
    }
    .cluster-card {
      min-height: 150px;
      padding: 16px 18px;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.045);
    }
    .cluster-card::before {
      display: none;
    }
    .cluster-card h3 {
      font-size: 16px;
    }
    .feed-panel {
      padding: 12px;
      border-color: #e3e9ec;
      background: #fff;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.045);
    }
    .feed-grid {
      gap: 12px;
    }
    .intel-card {
      min-height: 260px;
      padding: 14px;
      background: #fff;
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
    }
    .intel-card[data-category="ai"],
    .intel-card[data-category="open_source"],
    .intel-card[data-category="world_news"],
    .intel-card[data-category="technology"],
    .intel-card[data-category="programming"] {
      background: #fff;
    }
    .card-actions {
      gap: 6px;
    }
    @media (max-width: 1500px) {
      .app-shell {
        padding: 20px;
      }
      .topbar-inner {
        grid-template-columns: minmax(380px, 1fr) auto;
      }
      .brand-row .subtitle {
        margin-left: 28px;
      }
      .dashboard-layout {
        grid-template-columns: 250px minmax(0, 1fr);
        gap: 20px;
      }
      .command {
        grid-template-columns: 160px 174px minmax(180px, 1fr) 72px 68px;
        gap: 10px;
      }
      .metrics {
        grid-template-columns: repeat(5, minmax(0, 1fr));
      }
      .metric {
        grid-template-columns: 40px minmax(0, 1fr);
        gap: 9px;
        padding: 11px;
      }
      .metric-icon {
        width: 40px;
        height: 40px;
      }
      .metric-icon svg {
        width: 19px;
        height: 19px;
      }
      .metric strong {
        font-size: 22px;
      }
      .cluster-strip {
        grid-template-columns: repeat(2, minmax(240px, 1fr));
      }
      .feed-grid {
        grid-template-columns: repeat(2, minmax(250px, 1fr));
      }
      .llm-panel pre {
        max-width: calc(100% - 90px);
      }
      .alerts-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    @media (max-width: 1080px) {
      .metrics { grid-template-columns: repeat(3, minmax(130px, 1fr)); }
      .runtime-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .cluster-strip { grid-template-columns: repeat(2, minmax(210px, 1fr)); }
      .sources-grid { grid-template-columns: 1fr; }
      .dashboard-layout { grid-template-columns: 1fr; }
      .rail { position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .alerts-grid, .drawer-judgement { grid-template-columns: 1fr; }
    }
    @media (max-width: 760px) {
      .topbar-inner, .brand-row, .command, .metrics, .cluster-strip, .rail, .config-grid, .watch-target-row {
        grid-template-columns: 1fr;
      }
      .watch-history-row {
        grid-template-columns: 1fr;
      }
      .watch-history-meta {
        text-align: left;
      }
      .watch-target-keywords, .watch-target-description {
        grid-column: auto;
      }
      .watch-target-remove {
        justify-self: end;
      }
      .brand-row .subtitle { margin-left: 0; }
      .top-actions, .feed-actions { justify-content: flex-start; }
      .feed-grid { grid-template-columns: 1fr; }
      .command, .feed-head { padding: 12px; }
      .metric { min-height: 84px; }
      .runtime-grid { grid-template-columns: 1fr; }
      .alerts-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand-row">
        <div class="brand-logo" aria-hidden="true">
          <svg viewBox="0 0 64 64" focusable="false">
            <rect width="64" height="64" rx="16" fill="#f8fbfb" />
            <path d="M16 10h25l13 13v31H16z" fill="#0f8f8a" />
            <path d="M41 10v10c0 3 2 5 5 5h8z" fill="#6ee7db" />
            <path d="M24 22v17c0 6 4 9 10 9h11V30" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" />
            <path d="M31 31h8M31 38h8" fill="none" stroke="#6ee7db" stroke-width="3" stroke-linecap="round" />
            <rect x="43" y="27" width="7" height="7" rx="1.5" fill="#6ee7db" />
          </svg>
        </div>
        <div class="brand-copy">
          <div class="eyebrow">观微知著 · 私有情报台</div>
          <h1>知微情报中枢</h1>
        </div>
        <div class="subtitle" id="subtitle">加载中</div>
      </div>
      <div class="top-actions">
        <button id="favoritesBtn" class="soft icon-action"><span data-icon="star"></span>收藏</button>
        <button id="ignoredBtn" class="soft icon-action"><span data-icon="ban"></span>忽略项</button>
        <button id="configBtn" class="soft icon-action"><span data-icon="settings"></span>配置</button>
        <button id="runBtn" class="primary icon-action"><span data-icon="refresh"></span>更新情报</button>
      </div>
    </div>
  </header>

  <main class="app-shell">
    <div class="dashboard-layout">
      <aside class="rail">
        <section class="rail-section rail-nav">
          <h2>导航</h2>
          <div class="view-nav" id="viewNav">
            <button class="view-nav-btn active" data-view-nav="overview" type="button"><span data-icon="grid"></span><b>概览</b><small>总体、摘要、提醒</small></button>
            <button class="view-nav-btn" data-view-nav="today" type="button"><span data-icon="doc"></span><b>今日情报</b><small>筛选、主线、列表</small></button>
            <button class="view-nav-btn" data-view-nav="watch" type="button"><span data-icon="target"></span><b>观察雷达</b><small>对象走势和详情</small></button>
            <button class="view-nav-btn" data-view-nav="sources" type="button"><span data-icon="sparkles"></span><b>来源状态</b><small>来源、趋势、周报</small></button>
          </div>
        </section>
        <section class="rail-section rail-category" data-views="today">
          <h2>分类</h2>
          <div class="category-list" id="categoryButtons"></div>
        </section>
        <section class="rail-section rail-health" data-views="rail-extra">
          <h2>来源状态</h2>
          <div class="health" id="health"></div>
        </section>
        <section class="rail-section rail-trend" data-views="rail-extra">
          <h2>近期趋势</h2>
          <div class="trend" id="trend"></div>
        </section>
        <section class="rail-section rail-weekly" data-views="rail-extra">
          <h2>本周沉淀</h2>
          <div class="weekly" id="weekly"></div>
        </section>
      </aside>

      <section class="main-area">
        <section class="command" data-views="today">
          <select id="dateSelect"></select>
          <select id="categorySelect">
            <option value="">全部分类</option>
            <option value="ai">AI 与论文</option>
            <option value="open_source">开源项目</option>
            <option value="technology">技术热点</option>
            <option value="programming">编程与工程</option>
            <option value="world_news">全球时事</option>
          </select>
          <input id="searchInput" type="search" placeholder="搜索标题、摘要、关键词、项目或来源...">
          <button id="searchBtn" class="primary">搜索</button>
          <button id="clearBtn">清空</button>
        </section>

        <section class="metrics" id="stats" data-views="overview"></section>
        <section class="runtime-panel" id="runtimePanel" data-views="overview">
          <div class="runtime-head">
            <h2>运行状态</h2>
            <span class="meta" id="runtimeUpdated">等待状态</span>
          </div>
          <div class="runtime-grid" id="runtimeGrid"></div>
        </section>
        <section class="progress-panel" id="progressPanel">
          <div class="progress-head">
            <span class="progress-message" id="progressMessage">等待重跑</span>
            <span id="progressPercent">0%</span>
          </div>
          <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
          <div class="source-progress" id="sourceProgress"></div>
        </section>
        <section class="llm-panel" id="llmPanel" data-views="overview">
          <div class="llm-head">
            <h2>LLM 摘要</h2>
            <span id="llmTime"></span>
          </div>
          <pre id="llmSummary"></pre>
          <div class="sparkline" aria-hidden="true"></div>
        </section>
        <section class="alerts-panel" id="alertsPanel" data-views="overview">
          <div class="alerts-head">
            <h2>高价值信号</h2>
            <span id="alertsNote">规则筛选，模型判断</span>
          </div>
          <div class="alerts-grid" id="alerts"></div>
        </section>

        <section class="watch-panel" id="watchPanel" data-views="watch">
          <div class="watch-head">
            <h2>观察雷达</h2>
            <span id="watchNote">长期关注对象的动态</span>
          </div>
          <div class="watch-grid" id="watchRadar"></div>
          <div class="watch-history" id="watchHistory"></div>
        </section>

        <section class="sources-panel" data-views="sources">
          <div class="section-head">
            <h2>来源状态</h2>
            <div class="section-note">抓取健康、近期趋势和本周沉淀</div>
          </div>
          <div class="sources-grid">
            <section class="rail-section">
              <h2>来源健康</h2>
              <div class="health" id="sourcesHealth"></div>
            </section>
            <section class="rail-section">
              <h2>近期趋势</h2>
              <div class="trend" id="sourcesTrend"></div>
            </section>
            <section class="rail-section">
              <h2>本周沉淀</h2>
              <div class="weekly" id="sourcesWeekly"></div>
            </section>
          </div>
        </section>

        <section class="mainline-block" data-views="today">
          <div class="section-head">
            <h2>今日主线</h2>
            <div class="section-note" id="clusterNote">按相似主题聚合</div>
          </div>
          <div class="cluster-strip" id="clusters"></div>
        </section>

        <section class="feed-panel" data-views="today">
          <div class="feed-head">
            <h2 id="listTitle">重点排序</h2>
            <div class="feed-actions">
              <button id="refreshBtn" title="只刷新本地数据库里的页面数据，不会重新抓取互联网">刷新本地数据</button>
              <div class="view-toggle" aria-label="列表视图">
                <button id="gridViewBtn" class="active" type="button" title="卡片视图"><span data-icon="grid"></span></button>
                <button id="listViewBtn" type="button" title="列表视图"><span data-icon="list"></span></button>
              </div>
            </div>
          </div>
          <div class="filter-row">
            <div class="bucket-tabs" id="bucketTabs"></div>
            <div class="read-tabs" id="readTabs"></div>
          </div>
          <div class="feed-grid" id="items"></div>
        </section>
      </section>
    </div>
  </main>

  <div class="drawer-backdrop" id="drawerBackdrop"></div>
  <div class="toast" id="toast"></div>

  <aside class="drawer" id="detailDrawer" aria-label="详情">
    <div class="drawer-inner">
      <div class="drawer-head">
        <h2 id="detailTitle">详情</h2>
        <button id="closeDetailBtn">关闭</button>
      </div>
      <div id="detailBody"></div>
    </div>
  </aside>

  <aside class="drawer" id="configDrawer" aria-label="配置">
    <div class="drawer-inner">
      <div class="drawer-head">
        <h2>配置中心</h2>
        <div class="top-actions">
          <button id="saveConfigBtn" class="primary">保存</button>
          <button id="closeConfigBtn">关闭</button>
        </div>
      </div>
      <div class="switch-row">
        <label><input id="cfgGithubEnabled" type="checkbox">GitHub Trending</label>
        <label><input id="cfgArxivEnabled" type="checkbox">arXiv</label>
        <label><input id="cfgGdeltEnabled" type="checkbox">GDELT</label>
        <label><input id="cfgRssEnabled" type="checkbox">RSS</label>
        <label><input id="cfgLlmEnabled" type="checkbox">LLM 总览</label>
        <label><input id="cfgTranslationEnabled" type="checkbox">全球时事翻译</label>
      </div>
      <div class="theme-field">
        <span>页面主题</span>
        <div class="theme-switch" id="themeSwitch" aria-label="主题">
          <button data-theme="focus" type="button">浅色</button>
          <button data-theme="night" type="button">夜间</button>
          <button data-theme="compact" type="button">紧凑</button>
        </div>
      </div>
      <div class="drawer-section">
        <div class="config-section-head">
          <h3>观察清单</h3>
          <button id="addWatchTargetBtn" class="soft icon-action" type="button"><span data-icon="target"></span>新增观察对象</button>
        </div>
        <div id="watchlistEditor" class="watchlist-editor"></div>
      </div>
      <div class="config-grid">
        <div class="field"><label>每天运行时间</label><input id="cfgDailyTime" placeholder="08:30"></div>
        <div class="field"><label>抓取最近几天</label><input id="cfgDaysBack" type="number" min="1"></div>
        <div class="field"><label>GitHub Trending 周期</label><select id="cfgTrendingSince"><option value="daily">daily</option><option value="weekly">weekly</option><option value="monthly">monthly</option></select></div>
        <div class="field"><label>全球时事翻译 API</label><select id="cfgTranslationProvider"><option value="public">public</option><option value="mimo">mimo</option></select></div>
        <div class="field"><label>LLM 分析条目数</label><input id="cfgLlmMaxItems" type="number" min="1" max="100"></div>
        <div class="field"><label>LLM 输出 Token 上限</label><input id="cfgLlmMaxTokens" type="number" min="1000" step="1000"></div>
        <div class="field"><label>GitHub Trending 语言</label><textarea id="cfgTrendingLanguages"></textarea></div>
        <div class="field"><label>arXiv 分类</label><textarea id="cfgArxivCategories"></textarea></div>
        <div class="field"><label>arXiv 关键词</label><textarea id="cfgArxivKeywords"></textarea></div>
        <div class="field"><label>GDELT 新闻关键词</label><textarea id="cfgGdeltQueries"></textarea></div>
        <div class="field"><label>RSS 源：name | url | category</label><textarea id="cfgRssFeeds"></textarea></div>
        <div class="field"><label>优先关注主题</label><textarea id="cfgPriorityTopics"></textarea></div>
        <div class="field"><label>屏蔽关键词</label><textarea id="cfgBlockedKeywords"></textarea></div>
        <div class="field"><label>偏好高质量来源域名</label><textarea id="cfgPreferredDomains"></textarea></div>
        <div class="field"><label>屏蔽域名</label><textarea id="cfgBlockedDomains"></textarea></div>
        <div class="field"><label>新鲜度权重</label><input id="cfgWeightFreshness" type="number" step="0.01" min="0" max="1"></div>
        <div class="field"><label>来源质量权重</label><input id="cfgWeightSource" type="number" step="0.01" min="0" max="1"></div>
        <div class="field"><label>兴趣匹配权重</label><input id="cfgWeightInterest" type="number" step="0.01" min="0" max="1"></div>
        <div class="field"><label>热度权重</label><input id="cfgWeightPopularity" type="number" step="0.01" min="0" max="1"></div>
      </div>
    </div>
  </aside>

  <script>
    const state = {
      date: "",
      category: "",
      bucket: "",
      readStatus: "",
      query: "",
      favorite: false,
      includeIgnored: false,
      theme: "focus",
      view: "overview",
      viewMode: "grid",
      configLoaded: false,
      pollTimer: null,
      stats: {}
    };

    const labels = {
      ai: "AI 与论文",
      open_source: "开源项目",
      technology: "技术热点",
      programming: "编程与工程",
      world_news: "全球时事",
      general: "其他"
    };

    const categoryOrder = ["", "ai", "open_source", "world_news", "technology", "programming"];
    const bucketLabels = {
      must: "必看",
      scan: "可扫",
      archive: "归档"
    };
    const bucketOrder = ["", "must", "scan", "archive"];
    const readLabels = {
      unread: "未读",
      read: "已读",
      later: "稍后看",
      archived: "已归档"
    };
    const readOrder = ["", "unread", "later", "read", "archived"];
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[ch]));

    const icons = {
      ban: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"></circle><path d="m8.5 8.5 7 7"></path></svg>',
      check: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"></circle><path d="m8 12 2.6 2.6L16.5 8.8"></path></svg>',
      doc: '<svg viewBox="0 0 24 24"><path d="M7 3.8h7l3 3V20H7z"></path><path d="M14 3.8V7h3"></path><path d="M9.5 12h5"></path><path d="M9.5 15h4"></path></svg>',
      flame: '<svg viewBox="0 0 24 24"><path d="M12 21c-3.4 0-6-2.5-6-5.8 0-2.2 1.2-4.2 3.1-5.4.4 1.4 1.2 2.2 2.4 2.6-.5-2.7.4-5 2.8-6.8 1 2.7 3.7 4.3 3.7 8.5 0 3.9-2.7 6.9-6 6.9z"></path><circle cx="12" cy="15" r="2.5"></circle></svg>',
      grid: '<svg viewBox="0 0 24 24"><path d="M4 4h6v6H4z"></path><path d="M14 4h6v6h-6z"></path><path d="M4 14h6v6H4z"></path><path d="M14 14h6v6h-6z"></path></svg>',
      list: '<svg viewBox="0 0 24 24"><path d="M8 6h12"></path><path d="M8 12h12"></path><path d="M8 18h12"></path><path d="M4 6h.01"></path><path d="M4 12h.01"></path><path d="M4 18h.01"></path></svg>',
      refresh: '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.7"></path><path d="M20 4v5h-5"></path></svg>',
      settings: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"></circle><path d="M12 3v2"></path><path d="M12 19v2"></path><path d="M4.2 7.5 6 8.5"></path><path d="m18 15.5 1.8 1"></path><path d="m4.2 16.5 1.8-1"></path><path d="m18 8.5 1.8-1"></path></svg>',
      sparkles: '<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"></path><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"></path></svg>',
      star: '<svg viewBox="0 0 24 24"><path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z"></path></svg>',
      target: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"></circle><circle cx="12" cy="12" r="3"></circle><path d="M12 2v3"></path><path d="M12 19v3"></path><path d="M2 12h3"></path><path d="M19 12h3"></path></svg>',
      trash: '<svg viewBox="0 0 24 24"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M6 6l1 15h10l1-15"></path><path d="M10 11v6"></path><path d="M14 11v6"></path></svg>'
    };

    function iconSvg(name) {
      return icons[name] || "";
    }

    function hydrateIcons(root = document) {
      root.querySelectorAll("[data-icon]").forEach((node) => {
        node.innerHTML = iconSvg(node.dataset.icon || "");
      });
    }

    async function api(path, options) {
      const response = await fetch(path, options);
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }

    async function loadDates() {
      const data = await api("/api/dates");
      const dates = data.dates || [];
      state.date = state.date || data.latest || dates[0] || "";
      $("dateSelect").innerHTML = dates.map((date) => `<option value="${esc(date)}">${esc(date)}</option>`).join("");
      syncDateSelect(state.date);
    }

    function syncDateSelect(date) {
      if (!date) return;
      const select = $("dateSelect");
      if (![...select.options].some((option) => option.value === date)) {
        const option = document.createElement("option");
        option.value = date;
        option.textContent = date;
        select.prepend(option);
      }
      select.value = date;
    }

    async function loadStats() {
      const params = new URLSearchParams({ date: state.date });
      const data = await api(`/api/stats?${params}`);
      state.stats = data;
      if (!state.date && data.report_date) {
        state.date = data.report_date;
        syncDateSelect(state.date);
      }
      const run = data.run || {};
      const reportDate = state.date || data.report_date || "";
      $("subtitle").textContent = reportDate ? `${reportDate} · 北京时间 ${formatBeijingClock(run.created_at || "")}` : "暂无日报";
      $("llmTime").textContent = run.created_at ? `生成时间：${formatShortBeijingTime(run.created_at)}` : "";
      $("runBtn").innerHTML = `<span data-icon="refresh">${iconSvg("refresh")}</span>${data.running ? "更新中" : "更新情报"}`;
      $("runBtn").disabled = !!data.running;
      $("stats").innerHTML = [
        metric("原始条目", run.raw_total ?? 0, "较昨日 -", "doc", "teal"),
        metric("有效条目", run.deduped_total ?? 0, "较昨日 -", "check", "blue"),
        metric("事件主线", data.cluster_count ?? 0, "较昨日 -", "target", "amber"),
        metric("收藏", data.mark_counts?.favorites ?? 0, "较昨日 -", "star", "plum"),
        metric("LLM 摘要", llmState(run.llm_summary || ""), "状态就绪", "sparkles", "teal")
      ].join("");
      renderLlmSummary(run.llm_summary || "");
      renderRunProgress(data.progress || {});
      renderBucketTabs(data.bucket_counts || []);
      renderReadTabs(data.read_status_counts || []);
      renderCategories(data.category_counts || []);
      renderHealth(data.source_health || []);
      renderWatchRadar(data);
    }

    async function loadRuntimeStatus() {
      const data = await api("/api/runtime-status");
      renderRuntimeStatus(data);
    }

    async function safeLoadRuntimeStatus() {
      try {
        await loadRuntimeStatus();
      } catch (error) {
        $("runtimeUpdated").textContent = "运行状态暂不可用";
        $("runtimeGrid").innerHTML = `
          <div class="runtime-card">
            <b>运行状态</b>
            <span class="runtime-warn" title="暂不可用">暂不可用</span>
          </div>
        `;
      }
    }

    function renderRuntimeStatus(data) {
      const lastRun = data.last_run || {};
      const sourceRows = lastRun.source_health || [];
      const failedSources = sourceRows.filter((row) => row.status !== "ok");
      const sourceText = sourceRows.length
        ? `${sourceRows.length - failedSources.length}/${sourceRows.length} 正常`
        : "暂无来源记录";
      const dashboardStatus = data.web?.status || data.dashboard?.status;
      const cards = [
        ["仪表盘", processLabel(dashboardStatus), processClass(dashboardStatus)],
        ["自动更新", schedulerLabel(data.scheduler?.status), schedulerClass(data.scheduler?.status)],
        ["上次运行", lastRunLabel(lastRun), lastRun.status === "error" ? "runtime-error" : processClass(lastRun.status)],
        ["下次运行", formatDateTime(data.next_run_at), "runtime-ok"],
        ["来源", sourceText, failedSources.length ? "runtime-error" : "runtime-ok"]
      ];
      $("runtimeGrid").innerHTML = cards.map(([label, value, className]) => `
        <div class="runtime-card">
          <b>${esc(label)}</b>
          <span class="${esc(className)}" title="${esc(value)}">${esc(value)}</span>
        </div>
      `).join("");
      $("runtimeUpdated").textContent = `时区：${data.timezone || ""}`;
    }

    function processLabel(status) {
      return {
        running: "运行中",
        stopped: "已停止",
        not_tracked: "未启动",
        invalid: "PID 无效",
        listening: "运行中",
        unreachable: "未监听",
        ok: "正常",
        error: "异常",
        not_initialized: "未初始化"
      }[status] || "未知";
    }

    function schedulerLabel(status) {
      return {
        running: "定时运行中",
        stopped: "已停止",
        not_tracked: "手动模式",
        invalid: "状态异常"
      }[status] || processLabel(status);
    }

    function schedulerClass(status) {
      if (status === "not_tracked") return "runtime-warn";
      return processClass(status);
    }

    function processClass(status) {
      if (["running", "listening", "ok"].includes(status)) return "runtime-ok";
      if (["not_tracked", "not_initialized"].includes(status)) return "runtime-warn";
      return "runtime-error";
    }

    function lastRunLabel(lastRun) {
      if (!lastRun || !lastRun.status || lastRun.status === "not_initialized") return "暂无运行";
      const when = formatDateTime(lastRun.created_at);
      if (lastRun.status === "error") return `${lastRun.report_date || ""} 异常`;
      return when || lastRun.report_date || "已运行";
    }

    function formatDateTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").slice(0, 16);
      return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    }

    function metric(label, value, delta, icon, tone) {
      return `
        <div class="metric metric-${esc(tone || "teal")}">
          <span class="metric-icon ${esc(tone || "")}">${iconSvg(icon)}</span>
          <div class="metric-copy">
            <strong>${esc(value)}</strong>
            <span class="metric-label">${esc(label)}</span>
            <small>${esc(delta || "")}</small>
          </div>
        </div>
      `;
    }

    function llmState(summary) {
      if (!summary) return "未生成";
      if (summary.startsWith("LLM failed") || summary.startsWith("LLM skipped")) return "已回退";
      return "已生成";
    }

    function renderLlmSummary(summary) {
      const raw = String(summary || "").trim();
      const text = cleanLlmSummary(raw);
      if (!text) {
        $("llmPanel").classList.remove("show");
        $("llmSummary").textContent = "";
        return;
      }
      $("llmPanel").classList.add("show");
      $("llmSummary").textContent = text;
      $("llmSummary").className = raw.startsWith("LLM failed") || raw.startsWith("LLM skipped") ? "llm-muted" : "";
    }

    function cleanLlmSummary(summary) {
      const lines = String(summary || "")
        .split(/\r?\n/)
        .filter((line) => !/^LLM\s*(模型|model)\s*[:：]/i.test(line.trim()));
      if (/^LLM\s*(failed|skipped)/i.test((lines[0] || "").trim())) {
        const fallbackIndex = lines.findIndex((line) => line.trim().startsWith("本地规则"));
        if (fallbackIndex >= 0) return lines.slice(fallbackIndex).join("\n").trim();
        return "LLM 暂不可用，已使用本地规则摘要。";
      }
      return lines.join("\n").trim();
    }

    function renderRunProgress(progress) {
      const running = !!state.stats.running;
      const status = progress.status || "";
      const shouldShow = running || status === "error";
      $("progressPanel").classList.toggle("show", shouldShow);
      if (!shouldShow) return;
      const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
      $("progressMessage").textContent = progress.message || "正在运行";
      $("progressPercent").textContent = `${Math.round(percent)}%`;
      $("progressFill").style.width = `${percent}%`;
      const sources = progress.sources || {};
      $("sourceProgress").innerHTML = Object.entries(sources).map(([source, row]) => {
        const statusClass = row.status === "ok" ? "ok" : (row.status === "error" ? "error" : "");
        const count = Number(row.count || 0);
        const suffix = row.status === "running" ? "进行中" : `${row.status || ""}${count ? ` · ${count}` : ""}`;
        return `<span class="${statusClass}">${esc(source)} · ${esc(suffix)}</span>`;
      }).join("");
    }

    function renderBucketTabs(counts) {
      const countMap = Object.fromEntries(counts.map((row) => [row.bucket || "scan", row.count]));
      const total = Object.values(countMap).reduce((sum, value) => sum + Number(value || 0), 0);
      $("bucketTabs").innerHTML = bucketOrder.map((bucket) => {
        const label = bucket ? bucketLabels[bucket] || bucket : "全部";
        const count = bucket ? countMap[bucket] || 0 : total;
        return `<button class="${state.bucket === bucket ? "active" : ""}" data-bucket="${esc(bucket)}">${esc(label)} · ${esc(count)}</button>`;
      }).join("");
    }

    function renderReadTabs(counts) {
      const countMap = Object.fromEntries(counts.map((row) => [row.read_status || "unread", row.count]));
      const total = Object.values(countMap).reduce((sum, value) => sum + Number(value || 0), 0);
      $("readTabs").innerHTML = readOrder.map((status) => {
        const label = status ? readLabels[status] || status : "全部状态";
        const count = status ? countMap[status] || 0 : total;
        return `<button class="${state.readStatus === status ? "active" : ""}" data-read-status="${esc(status)}">${esc(label)} · ${esc(count)}</button>`;
      }).join("");
    }

    function formatBeijingTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false
      }).format(date).replace(/\//g, "-");
    }

    function formatShortBeijingTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
      }).format(date);
    }

    function formatBeijingClock(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false
      }).format(date);
    }

    function renderCategories(counts) {
      const countMap = Object.fromEntries(counts.map((row) => [row.category, row.count]));
      $("categoryButtons").innerHTML = categoryOrder.map((category) => {
        const label = category ? labels[category] || category : "全部";
        const count = category ? countMap[category] || 0 : Object.values(countMap).reduce((sum, row) => sum + Number(row || 0), 0);
        return `<button class="category-btn ${state.category === category ? "active" : ""}" data-category="${esc(category)}"><span class="category-label">${esc(label)}</span><span class="category-count">${esc(count)}</span></button>`;
      }).join("");
    }

    function renderHealth(rows) {
      const html = rows.length ? rows.map((row) => `
        <div class="health-row">
          <span class="health-source"><i></i><span><b>${esc(sourceName(row.source))}</b><small class="${row.status === "ok" ? "health-ok" : "health-error"}">${esc(row.status)}</small></span></span>
          <span class="health-count">${esc(row.count)}</span>
        </div>
      `).join("") : "<div class='empty'>暂无状态</div>";
      $("health").innerHTML = html;
      $("sourcesHealth").innerHTML = html;
    }

    function renderWatchRadar(data) {
      const rows = data.watch_radar || [];
      const historyRows = data.watch_radar_history || [];
      $("watchPanel").classList.toggle("show", rows.length > 0 || historyRows.length > 0);
      $("watchNote").textContent = rows.length ? `${rows.length} 个观察对象` : "长期关注对象的动态";
      const grid = $("watchRadar");
      grid.className = `watch-grid count-${Math.min(rows.length, 6)}`;
      grid.innerHTML = rows.length ? rows.map((row) => `
        <article class="watch-card">
          <strong>${esc(row.status === "active" ? "有变化" : "暂无动向")}</strong>
          <button class="watch-target-link" data-watch-target="${esc(row.target_id || "")}" type="button">${esc(row.name || row.target_id || "观察对象")}</button>
          <p>${esc(row.summary || "")}</p>
          <p>${esc(row.action || "持续观察")} · 命中 ${esc(row.match_count || 0)} 条 · 置信度 ${Math.round(Number(row.confidence || 0) * 100)}%</p>
          ${row.url ? `<a class="watch-item-link" href="${esc(row.url)}" target="_blank" rel="noreferrer" data-open="${esc(row.item_hash || "")}">代表条目：${esc(row.item_title || "打开原文")}</a>` : ""}
        </article>
      `).join("") : "";
      renderWatchHistory(historyRows);
    }

    function renderWatchHistory(rows) {
      const panel = $("watchHistory");
      if (!rows.length) {
        panel.classList.remove("show");
        panel.innerHTML = "";
        return;
      }
      panel.classList.add("show");
      panel.innerHTML = `
        <div class="watch-history-head">
          <h3>近 7 次走势</h3>
          <span>${esc(rows.length)} 个观察对象</span>
        </div>
        ${rows.map((row) => `
          <button class="watch-history-row" data-watch-target="${esc(row.target_id || "")}" type="button">
            <div class="watch-history-name">
              <strong>${esc(row.name || row.target_id || "观察对象")}</strong>
              <span>${esc(row.latest_status === "active" ? "最近有变化" : "最近暂无动向")} · ${esc(row.latest_action || "持续观察")}</span>
            </div>
            <div class="watch-dots">${renderWatchDots(row.history || [])}</div>
            <div class="watch-history-meta">
              活跃 ${esc(row.active_days || 0)} 天 · 命中 ${esc(row.total_matches || 0)} 条 · 置信度 ${Math.round(Number(row.max_confidence || 0) * 100)}%
            </div>
          </button>
        `).join("")}
      `;
    }

    function renderWatchDots(history) {
      return history.map((point) => {
        const active = point.status === "active";
        const title = `${point.report_date || ""} · ${active ? "有变化" : "暂无动向"} · 命中 ${point.match_count || 0} 条 · 置信度 ${Math.round(Number(point.confidence || 0) * 100)}%`;
        return `<span class="watch-dot ${active ? "active" : ""}" title="${esc(title)}"></span>`;
      }).join("");
    }

    function sourceName(source) {
      return {
        arxiv: "arXiv",
        gdelt: "GDELT",
        github: "GitHub",
        github_trending: "GitHub Trending",
        hackernews: "Hacker News",
        rss: "RSS"
      }[source] || source || "未知来源";
    }

    function showToast(message, sticky = false) {
      $("toast").textContent = message;
      $("toast").classList.add("show");
      if (!sticky) {
        setTimeout(() => $("toast").classList.remove("show"), 3600);
      }
    }

    function startRunPolling() {
      if (state.pollTimer) clearInterval(state.pollTimer);
      state.pollTimer = setInterval(async () => {
        await loadStats();
        if (!state.stats.running) {
          clearInterval(state.pollTimer);
          state.pollTimer = null;
          await Promise.all([loadClusters(), loadItems(), loadTrends(), loadWeekly(), loadAlerts()]);
          showToast("重跑完成，页面已刷新。");
        }
      }, 5000);
    }

    async function loadTrends() {
      const data = await api("/api/trends");
      const rows = data.trends || [];
      if (!rows.length) {
        const empty = "<div class='empty'>暂无趋势</div>";
        $("trend").innerHTML = empty;
        $("sourcesTrend").innerHTML = empty;
        return;
      }
      const latest = rows[rows.length - 1] || {};
      const previous = rows[rows.length - 2] || {};
      const total = Number(latest.deduped_total || 0);
      const delta = total - Number(previous.deduped_total || total);
      const points = sparklinePoints(rows.map((row) => Number(row.deduped_total || 0)), 216, 72);
      const html = `
        <div class="trend-card">
          <div class="trend-top">
            <div><b>${esc(total)}</b><small>较昨日 ${delta >= 0 ? "+" : ""}${esc(delta)}</small></div>
            <span>近 7 天</span>
          </div>
          <div class="trend-chart-shell">
            <svg class="trend-chart" viewBox="0 0 216 72" preserveAspectRatio="none">
              <polyline points="${esc(points)}"></polyline>
            </svg>
          </div>
          <div class="trend-scale"><span>${esc(rows[0]?.report_date?.slice(5) || "")}</span><span>${esc(latest.report_date?.slice(5) || "")}</span></div>
        </div>
      `;
      $("trend").innerHTML = html;
      $("sourcesTrend").innerHTML = html;
    }

    function sparklinePoints(values, width, height) {
      const rows = values.length ? values : [0];
      const max = Math.max(...rows, 1);
      const min = Math.min(...rows, 0);
      const range = Math.max(1, max - min);
      return rows.map((value, index) => {
        const x = rows.length === 1 ? width : index / (rows.length - 1) * width;
        const y = height - ((value - min) / range * (height - 12) + 6);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
    }

    async function loadWeekly() {
      const params = new URLSearchParams();
      if (state.date) params.set("date", state.date);
      const query = params.toString();
      const data = await api(`/api/weekly${query ? `?${query}` : ""}`);
      renderWeekly(data.weekly || {});
    }

    function renderWeekly(weekly) {
      if (!weekly.week_id) {
        const empty = "<div class='empty'>暂无周报</div>";
        $("weekly").innerHTML = empty;
        $("sourcesWeekly").innerHTML = empty;
        return;
      }
      const tags = (weekly.top_tags || []).slice(0, 6).map((row) => `<span>${esc(row.tag)} · ${esc(row.count)}</span>`).join("");
      const must = findCount(weekly.bucket_counts || [], "must");
      const unread = findCount(weekly.read_status_counts || [], "unread");
      const html = `
        <article class="weekly-card">
          <div class="weekly-head">
            <a href="${esc(weekly.html_url || "#")}" target="_blank" rel="noreferrer">${esc(weekly.week_id)} 周报</a>
            <div class="weekly-meta">${esc(weekly.label || "")}</div>
          </div>
          <div class="weekly-stats">
            <span><b>${esc(weekly.active_total || 0)}</b><em>有效条目</em></span>
            <span><b>${esc((weekly.report_dates || []).length)}</b><em>覆盖日报</em></span>
            <span><b>${esc(must)}</b><em>必看</em></span>
            <span><b>${esc(unread)}</b><em>未读</em></span>
          </div>
          <div class="weekly-tags">${tags || "<span>暂无热词</span>"}</div>
        </article>
      `;
      $("weekly").innerHTML = html;
      $("sourcesWeekly").innerHTML = html;
    }

    function findCount(rows, key) {
      const row = rows.find((item) => item.key === key);
      return row ? Number(row.count || 0) : 0;
    }

    async function loadClusters() {
      const params = new URLSearchParams();
      if (state.date) params.set("date", state.date);
      if (state.category) params.set("category", state.category);
      const data = await api(`/api/clusters?${params}`);
      const allClusters = data.clusters || [];
      const clusters = allClusters.slice(0, 4);
      $("clusterNote").textContent = clusters.length
        ? `已显示重点 ${clusters.length} 条主线`
        : "暂无主线";
      $("clusters").innerHTML = clusters.length ? clusters.map((cluster, index) => {
        const heat = Math.max(60, Math.min(99, Math.round(Number(cluster.score || 0))));
        return `
        <article class="cluster-card" data-category="${esc(cluster.category || "general")}">
          <div class="cluster-kicker">主线 · ${String(index + 1).padStart(2, "0")}</div>
          <h3><a href="${esc(cluster.top_url || "#")}" target="_blank" rel="noreferrer" data-open="${esc(cluster.top_hash || "")}">${esc(cluster.title)}</a></h3>
          <p>${esc(cluster.explanation || cluster.summary || "暂无解释")}</p>
          <div class="cluster-heat">热度 ${esc(heat)}</div>
          <button data-cluster-hash="${esc(cluster.top_hash || "")}">查看</button>
        </article>
      `}).join("") : "<div class='empty'>暂无可聚合主线</div>";
    }

    async function loadItems() {
      const params = new URLSearchParams();
      if (state.date) params.set("date", state.date);
      if (state.category) params.set("category", state.category);
      if (state.bucket) params.set("bucket", state.bucket);
      if (state.readStatus) params.set("read_status", state.readStatus);
      if (state.query) params.set("q", state.query);
      if (state.favorite) params.set("favorite", "1");
      if (state.includeIgnored) params.set("include_ignored", "1");
      const data = await api(`/api/items?${params}`);
      const items = data.items || [];
      $("listTitle").textContent = state.favorite ? "我的收藏" : (state.readStatus ? readLabels[state.readStatus] || state.readStatus : (state.bucket ? bucketLabels[state.bucket] || state.bucket : (state.category ? labels[state.category] || state.category : "重点排序")));
      $("items").classList.toggle("list", state.viewMode === "list");
      $("items").innerHTML = items.length ? items.map((item, index) => renderCard(item, index)).join("") : "<div class='empty'>没有匹配条目</div>";
    }

    function renderCard(item, index) {
      const tags = (item.tags || []).slice(0, 5).map((tag) => `<span>${esc(tag)}</span>`).join("");
      const summary = item.ai_summary || item.summary || "";
      const judgement = renderJudgement(item.judgement || fallbackJudgement(item));
      const cluster = Number(item.cluster_size || 1) > 1 ? ` · 事件组 ${item.cluster_size}` : "";
      return `
        <article class="intel-card" data-hash="${esc(item.hash)}" data-category="${esc(item.category || "general")}" data-bucket="${esc(item.bucket || "scan")}">
          <div class="card-top">
            <span class="source-pill">${esc(item.source)}</span>
            <span class="bucket-pill ${esc(item.bucket || "scan")}">${esc(bucketLabels[item.bucket] || "可扫")}</span>
            <span class="read-pill ${esc(item.read_status || "unread")}">${esc(readLabels[item.read_status] || "未读")}</span>
            <span class="score">rank ${esc(index + 1)}</span>
          </div>
          <h3><a href="${esc(item.url)}" target="_blank" rel="noreferrer" data-open="${esc(item.hash)}">${esc(item.title)}</a></h3>
          <p class="summary">${esc(summary)}</p>
          ${judgement}
          <div class="tags">${tags}</div>
          <div class="meta">${esc(labels[item.category] || item.category)} · importance ${esc(item.importance || 0)}/5${cluster}<br>${esc(item.published_at || "")}</div>
          <div class="card-actions">
            <button data-action="detail"><span data-icon="list">${iconSvg("list")}</span>详情</button>
            <button data-action="later"><span data-icon="star">${iconSvg("star")}</span>${item.read_status === "later" ? "取消稍后" : "稍后看"}</button>
            <button data-action="archive"><span data-icon="doc">${iconSvg("doc")}</span>${item.read_status === "archived" ? "取消归档" : "归档"}</button>
            <button data-action="favorite"><span data-icon="star">${iconSvg("star")}</span>${item.favorite ? "已收藏" : "收藏"}</button>
            <button data-action="ignore"><span data-icon="ban">${iconSvg("ban")}</span>${item.ignored ? "恢复" : "忽略"}</button>
            <a href="${esc(item.url)}" target="_blank" rel="noreferrer" data-open="${esc(item.hash)}">原文</a>
          </div>
        </article>
      `;
    }

    function fallbackJudgement(item) {
      return {
        recommendation: item.why || item.top_reason || "这条内容进入当前排序靠前位置，适合快速判断是否值得继续打开原文。",
        caveat: Number(item.cluster_size || 1) > 1 ? "已出现相关主线，可优先看主线脉络。" : "暂未形成多源印证，建议把它当作线索而不是结论。"
      };
    }

    function renderJudgement(judgement, extraClass = "") {
      const rows = [
        ["推荐理由", judgement.recommendation || "暂无推荐理由"],
        ["阅读提示", judgement.caveat || judgement.risk || ""]
      ].filter(([, value]) => String(value || "").trim());
      return `<div class="judgement-grid ${esc(extraClass)}">${rows.map(([label, value]) => `
        <div class="judgement-row"><b>${esc(label)}：</b><span>${esc(value)}</span></div>
      `).join("")}</div>`;
    }

    async function loadAlerts() {
      const params = new URLSearchParams();
      if (state.date) params.set("date", state.date);
      const data = await api(`/api/alerts?${params}`);
      const alerts = data.alerts || [];
      $("alertsPanel").classList.toggle("show", alerts.length > 0);
      $("alertsNote").textContent = alerts.length ? `${alerts.length} 条高价值信号` : "暂无需要提醒的信号";
      const alertsEl = $("alerts");
      alertsEl.className = `alerts-grid count-${Math.min(alerts.length, 6)}`;
      alertsEl.innerHTML = alerts.map((alert) => renderAlert(alert)).join("");
    }

    function renderAlert(alert) {
      const kindLabels = {
        llm_watch: "模型判断",
        github_spike: "GitHub 异动",
        paper_signal: "论文信号",
        multi_source: "多源主线",
        topic_watch: "主题触发"
      };
      const confidence = Number(alert.confidence || 0);
      const confidenceText = confidence > 0 ? `置信度 ${Math.round(confidence * 100)}%` : "";
      const detail = [alert.detail || "", alert.action || "", confidenceText].filter(Boolean).join(" · ");
      return `
        <article class="alert-card">
          <strong>${esc(kindLabels[alert.kind] || alert.title || "高价值信号")}</strong>
          <a href="${esc(alert.url || "#")}" target="_blank" rel="noreferrer" data-open="${esc(alert.item_hash || "")}">${esc(alert.item_title || alert.title || "查看条目")}</a>
          <p>${esc(detail)}</p>
        </article>
      `;
    }

    async function openDetail(hash) {
      if (!hash) return;
      await recordEvent(hash, "detail");
      const params = new URLSearchParams({ hash, date: state.date });
      const data = await api(`/api/item?${params}`);
      const item = data.item || {};
      const related = data.related || [];
      $("detailTitle").innerHTML = `<a href="${esc(item.url || "#")}" target="_blank" rel="noreferrer" data-open="${esc(item.hash || "")}">${esc(item.title || "详情")}</a>`;
      $("detailBody").innerHTML = `
        <div class="drawer-section">
          <div class="meta">${esc(readLabels[item.read_status] || "未读")} · ${esc(bucketLabels[item.bucket] || "可扫")} · ${esc(labels[item.category] || item.category)} · ${esc(item.source)} · rank ${Number(item.rank_score || 0).toFixed(1)} · importance ${esc(item.importance || 0)}/5</div>
          <div class="tags">${(item.tags || []).map((tag) => `<span>${esc(tag)}</span>`).join("")}</div>
        </div>
        <div class="drawer-section">
          <h3>摘要</h3>
          <div class="detail-text">${esc(item.ai_summary || item.summary || "暂无摘要")}</div>
        </div>
        <div class="drawer-section">
          <h3>为什么重要</h3>
          ${renderJudgement(item.judgement || fallbackJudgement(item), "drawer-judgement")}
        </div>
        <div class="drawer-section">
          <h3>内容</h3>
          <div class="detail-text">${esc(item.content || item.summary || "")}</div>
        </div>
        <div class="drawer-section">
          <h3>相关条目</h3>
          <div class="related-list">${related.length ? related.map((row) => `<button data-related-hash="${esc(row.hash)}">${esc(row.title)}</button>`).join("") : "<div class='empty'>暂无相关条目</div>"}</div>
        </div>
        <div class="drawer-section">
          <a href="${esc(item.url || "#")}" target="_blank" rel="noreferrer" data-open="${esc(item.hash || "")}">打开原文</a>
        </div>
      `;
      openDrawer("detailDrawer");
      await loadStats();
      await loadItems();
    }

    async function openWatchTargetDetail(targetId) {
      if (!targetId) return;
      const params = new URLSearchParams({ target: targetId, days: "7" });
      const data = await api(`/api/watch-target?${params}`);
      const target = data.target || {};
      const records = data.records || [];
      $("detailTitle").textContent = target.name || target.target_id || "观察对象";
      $("detailBody").innerHTML = `
        <div class="drawer-section">
          <div class="meta">
            ${esc(watchStatusLabel(target.latest_status))} · ${esc(target.latest_action || "持续观察")} · 最近 ${esc(target.latest_report_date || "-")} · 活跃 ${esc(target.active_days || 0)} 天 · 命中 ${esc(target.total_matches || 0)} 条 · 最高置信度 ${Math.round(Number(target.max_confidence || 0) * 100)}%
          </div>
        </div>
        <div class="drawer-section">
          <h3>最新判断</h3>
          ${records.length ? renderWatchTargetRecord(records[0], true) : "<div class='empty'>暂无判断记录</div>"}
        </div>
        <div class="drawer-section">
          <h3>近期记录</h3>
          <div class="watch-detail-records">
            ${records.length ? records.map((record) => renderWatchTargetRecord(record, false)).join("") : "<div class='empty'>暂无历史记录</div>"}
          </div>
        </div>
      `;
      openDrawer("detailDrawer");
    }

    function renderWatchTargetRecord(record, compact) {
      const confidence = Math.round(Number(record.confidence || 0) * 100);
      const title = record.item_title || "代表条目";
      const itemLink = record.url
        ? `<a href="${esc(record.url)}" target="_blank" rel="noreferrer" data-open="${esc(record.item_hash || "")}">${esc(title)}</a>`
        : "<span class='meta'>暂无代表条目</span>";
      return `
        <div class="watch-detail-record">
          <b>${esc(record.report_date || "-")} · ${esc(watchStatusLabel(record.status))} · ${esc(record.action || "持续观察")}</b>
          <div class="meta">命中 ${esc(record.match_count || 0)} 条 · 置信度 ${esc(confidence)}% · ${esc(sourceName(record.source || ""))} · score ${Number(record.score || 0).toFixed(1)}</div>
          <p>${esc(record.summary || "暂无摘要")}</p>
          ${compact ? `<p>${itemLink}</p>` : itemLink}
        </div>
      `;
    }

    function watchStatusLabel(status) {
      return status === "active" ? "有变化" : "暂无动向";
    }

    function openDrawer(id) {
      $("drawerBackdrop").classList.add("open");
      $(id).classList.add("open");
    }

    function closeDrawers() {
      $("drawerBackdrop").classList.remove("open");
      $("detailDrawer").classList.remove("open");
      $("configDrawer").classList.remove("open");
    }

    async function recordEvent(hash, type) {
      try {
        await api("/api/event", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hash, type })
        });
      } catch (error) {
        console.warn(error);
      }
    }

    async function toggleMark(hash, patch) {
      await api("/api/mark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hash, ...patch })
      });
      await loadStats();
      await loadItems();
    }

    async function loadConfig() {
      const config = await api("/api/config");
      $("cfgDailyTime").value = config.app?.daily_time || "08:30";
      $("cfgDaysBack").value = config.app?.days_back || 1;
      $("cfgGithubEnabled").checked = !!config.github?.enabled;
      $("cfgArxivEnabled").checked = !!config.arxiv?.enabled;
      $("cfgGdeltEnabled").checked = !!config.gdelt?.enabled;
      $("cfgRssEnabled").checked = !!config.rss?.enabled;
      $("cfgLlmEnabled").checked = !!config.llm?.enabled;
      $("cfgTranslationEnabled").checked = !!config.translation?.enabled;
      $("cfgTranslationProvider").value = config.translation?.provider || "public";
      $("cfgLlmMaxItems").value = config.llm?.max_items || 40;
      $("cfgLlmMaxTokens").value = config.llm?.max_tokens || 8000;
      $("cfgTrendingSince").value = config.github?.trending_since || "daily";
      $("cfgTrendingLanguages").value = joinLines(config.github?.trending_languages || [""]);
      $("cfgArxivCategories").value = joinLines(config.arxiv?.categories || []);
      $("cfgArxivKeywords").value = joinLines(config.arxiv?.keywords || []);
      $("cfgGdeltQueries").value = joinLines(config.gdelt?.queries || []);
      $("cfgRssFeeds").value = (config.rss?.feeds || []).map((feed) => `${feed.name || ""} | ${feed.url || ""} | ${feed.category || "general"}`).join("\n");
      $("cfgPriorityTopics").value = joinLines(config.interests?.priority_topics || []);
      $("cfgBlockedKeywords").value = joinLines(config.interests?.blocked_keywords || []);
      $("cfgPreferredDomains").value = joinLines(config.interests?.preferred_domains || []);
      $("cfgBlockedDomains").value = joinLines(config.interests?.blocked_domains || []);
      renderWatchlistEditor(config.interests?.watchlist || []);
      const weights = config.interests?.weights || {};
      $("cfgWeightFreshness").value = weights.freshness ?? 0.35;
      $("cfgWeightSource").value = weights.source_quality ?? 0.2;
      $("cfgWeightInterest").value = weights.personal_interest ?? 0.25;
      $("cfgWeightPopularity").value = weights.popularity ?? 0.15;
      state.configLoaded = true;
    }

    async function saveConfig() {
      const payload = {
        app: {
          daily_time: $("cfgDailyTime").value.trim() || "08:30",
          days_back: Number($("cfgDaysBack").value || 1)
        },
        github: {
          enabled: $("cfgGithubEnabled").checked,
          trending_since: $("cfgTrendingSince").value,
          trending_languages: splitLines($("cfgTrendingLanguages").value)
        },
        arxiv: {
          enabled: $("cfgArxivEnabled").checked,
          categories: splitLines($("cfgArxivCategories").value),
          keywords: splitLines($("cfgArxivKeywords").value)
        },
        gdelt: {
          enabled: $("cfgGdeltEnabled").checked,
          queries: splitLines($("cfgGdeltQueries").value)
        },
        rss: {
          enabled: $("cfgRssEnabled").checked,
          feeds: parseFeeds($("cfgRssFeeds").value)
        },
        llm: {
          enabled: $("cfgLlmEnabled").checked,
          max_items: Number($("cfgLlmMaxItems").value || 40),
          max_tokens: Number($("cfgLlmMaxTokens").value || 8000)
        },
        translation: {
          enabled: $("cfgTranslationEnabled").checked,
          provider: $("cfgTranslationProvider").value
        },
        interests: {
          priority_topics: splitLines($("cfgPriorityTopics").value),
          blocked_keywords: splitLines($("cfgBlockedKeywords").value),
          preferred_domains: splitLines($("cfgPreferredDomains").value),
          blocked_domains: splitLines($("cfgBlockedDomains").value),
          watchlist: readWatchlistEditor(),
          weights: {
            freshness: Number($("cfgWeightFreshness").value || 0.35),
            source_quality: Number($("cfgWeightSource").value || 0.2),
            personal_interest: Number($("cfgWeightInterest").value || 0.25),
            popularity: Number($("cfgWeightPopularity").value || 0.15)
          }
        }
      };
      await api("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      $("saveConfigBtn").textContent = "已保存";
      setTimeout(() => $("saveConfigBtn").textContent = "保存", 1400);
      await refresh();
    }

    function splitLines(value) {
      return String(value || "").split(/\n|,/).map((row) => row.trim()).filter(Boolean);
    }

    function joinLines(value) {
      return (value || []).join("\n");
    }

    function parseFeeds(value) {
      return String(value || "").split("\n").map((line) => {
        const parts = line.split("|").map((part) => part.trim());
        return { name: parts[0] || "", url: parts[1] || "", category: parts[2] || "general" };
      }).filter((feed) => feed.name && feed.url);
    }

    function renderWatchlistEditor(rows) {
      const editor = $("watchlistEditor");
      editor.innerHTML = "";
      const targets = Array.isArray(rows) ? rows : [];
      if (!targets.length) {
        addWatchTarget({ enabled: true, type: "topic" });
        return;
      }
      targets.forEach((target) => addWatchTarget(target));
    }

    function addWatchTarget(target = {}) {
      const editor = $("watchlistEditor");
      const row = document.createElement("div");
      row.className = "watch-target-row";
      row.dataset.watchId = target.id || "";
      row.innerHTML = `
        <label class="watch-target-enabled"><input data-watch-field="enabled" type="checkbox">启用</label>
        <div class="field"><label>名称</label><input data-watch-field="name" placeholder="AI Agent"></div>
        <div class="field">
          <label>类型</label>
          <select data-watch-field="type">
            <option value="topic">主题</option>
            <option value="project">项目</option>
            <option value="company">公司</option>
            <option value="person">人物</option>
            <option value="policy">政策</option>
          </select>
        </div>
        <div class="field watch-target-keywords"><label>关键词</label><textarea data-watch-field="keywords" placeholder="agent, workflow"></textarea></div>
        <div class="field watch-target-description"><label>描述</label><textarea data-watch-field="description" placeholder="关注范围"></textarea></div>
        <button class="watch-target-remove" data-watch-remove type="button" title="删除观察对象" aria-label="删除观察对象"><span data-icon="trash"></span></button>
      `;
      editor.appendChild(row);
      row.querySelector('[data-watch-field="enabled"]').checked = target.enabled !== false;
      row.querySelector('[data-watch-field="name"]').value = target.name || "";
      row.querySelector('[data-watch-field="keywords"]').value = joinLines(target.keywords || []);
      row.querySelector('[data-watch-field="description"]').value = target.description || "";
      setWatchTargetType(row.querySelector('[data-watch-field="type"]'), target.type || "topic");
      row.querySelector("[data-watch-remove]").addEventListener("click", () => row.remove());
      hydrateIcons(row);
    }

    function setWatchTargetType(select, value) {
      const next = value || "topic";
      if (![...select.options].some((option) => option.value === next)) {
        select.append(new Option(next, next));
      }
      select.value = next;
    }

    function readWatchlistEditor() {
      return [...$("watchlistEditor").querySelectorAll(".watch-target-row")].map((row) => ({
        id: row.dataset.watchId || "",
        name: row.querySelector('[data-watch-field="name"]').value.trim(),
        type: row.querySelector('[data-watch-field="type"]').value || "topic",
        enabled: row.querySelector('[data-watch-field="enabled"]').checked,
        keywords: splitLines(row.querySelector('[data-watch-field="keywords"]').value),
        description: row.querySelector('[data-watch-field="description"]').value.trim()
      })).filter((target) => target.name && target.keywords.length);
    }

    function loadTheme() {
      let saved = "focus";
      try {
        saved = localStorage.getItem("localIntelTheme") || "focus";
      } catch {
        saved = "focus";
      }
      setTheme(saved, false);
    }

    function setTheme(theme, persist = true) {
      const next = ["focus", "night", "compact"].includes(theme) ? theme : "focus";
      state.theme = next;
      document.documentElement.dataset.theme = next;
      if (persist) {
        try {
          localStorage.setItem("localIntelTheme", next);
        } catch {
          // localStorage can be unavailable in restricted browser contexts.
        }
      }
      $("themeSwitch").querySelectorAll("button[data-theme]").forEach((button) => {
        button.classList.toggle("active", button.dataset.theme === next);
      });
    }

    function loadDashboardView() {
      let saved = "overview";
      try {
        saved = localStorage.getItem("localIntelView") || "overview";
      } catch {
        saved = "overview";
      }
      setView(saved, false);
    }

    function setView(view, persist = true) {
      const next = ["overview", "today", "watch", "sources"].includes(view) ? view : "overview";
      state.view = next;
      document.querySelectorAll("[data-view-nav]").forEach((button) => {
        button.classList.toggle("active", button.dataset.viewNav === next);
      });
      document.querySelectorAll("[data-views]").forEach((node) => {
        const views = String(node.dataset.views || "").split(/\s+/).filter(Boolean);
        node.hidden = !views.includes(next);
      });
      if (persist) {
        try {
          localStorage.setItem("localIntelView", next);
        } catch {
          // localStorage can be unavailable in restricted browser contexts.
        }
      }
    }

    async function refresh() {
      await loadDates();
      await loadStats();
      await safeLoadRuntimeStatus();
      await Promise.all([loadClusters(), loadItems(), loadTrends(), loadWeekly(), loadAlerts()]);
    }

    $("dateSelect").addEventListener("change", async (event) => {
      state.date = event.target.value;
      await loadStats();
      await Promise.all([loadClusters(), loadItems(), loadWeekly(), loadAlerts()]);
    });
    $("categorySelect").addEventListener("change", async (event) => {
      state.category = event.target.value;
      await loadStats();
      await Promise.all([loadClusters(), loadItems()]);
    });
    $("categoryButtons").addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-category]");
      if (!button) return;
      state.category = button.dataset.category || "";
      $("categorySelect").value = state.category;
      await loadStats();
      await Promise.all([loadClusters(), loadItems()]);
    });
    $("bucketTabs").addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-bucket]");
      if (!button) return;
      state.bucket = button.dataset.bucket || "";
      renderBucketTabs(state.stats.bucket_counts || []);
      await loadItems();
    });
    $("readTabs").addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-read-status]");
      if (!button) return;
      state.readStatus = button.dataset.readStatus || "";
      renderReadTabs(state.stats.read_status_counts || []);
      await loadItems();
    });
    $("searchBtn").addEventListener("click", async () => {
      state.query = $("searchInput").value.trim();
      await loadItems();
    });
    $("searchInput").addEventListener("keydown", async (event) => {
      if (event.key === "Enter") {
        state.query = $("searchInput").value.trim();
        await loadItems();
      }
    });
    $("clearBtn").addEventListener("click", async () => {
      $("searchInput").value = "";
      state.query = "";
      await loadItems();
    });
    $("favoritesBtn").addEventListener("click", async () => {
      setView("today");
      state.favorite = !state.favorite;
      $("favoritesBtn").classList.toggle("active", state.favorite);
      await loadItems();
    });
    $("ignoredBtn").addEventListener("click", async () => {
      setView("today");
      state.includeIgnored = !state.includeIgnored;
      $("ignoredBtn").classList.toggle("active", state.includeIgnored);
      await loadItems();
    });
    $("configBtn").addEventListener("click", async () => {
      openDrawer("configDrawer");
      if (!state.configLoaded) await loadConfig();
    });
    $("closeConfigBtn").addEventListener("click", closeDrawers);
    $("closeDetailBtn").addEventListener("click", closeDrawers);
    $("drawerBackdrop").addEventListener("click", closeDrawers);
    $("saveConfigBtn").addEventListener("click", saveConfig);
    $("addWatchTargetBtn").addEventListener("click", () => addWatchTarget({ enabled: true, type: "topic" }));
    $("themeSwitch").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-theme]");
      if (!button) return;
      setTheme(button.dataset.theme || "focus");
    });
    $("gridViewBtn").addEventListener("click", async () => {
      state.viewMode = "grid";
      $("gridViewBtn").classList.add("active");
      $("listViewBtn").classList.remove("active");
      await loadItems();
    });
    $("listViewBtn").addEventListener("click", async () => {
      state.viewMode = "list";
      $("listViewBtn").classList.add("active");
      $("gridViewBtn").classList.remove("active");
      await loadItems();
    });
    $("refreshBtn").addEventListener("click", async () => {
      $("refreshBtn").disabled = true;
      $("refreshBtn").textContent = "刷新中";
      await refresh();
      $("refreshBtn").disabled = false;
      $("refreshBtn").textContent = "刷新本地数据";
      showToast("已刷新本地数据。要重新抓取互联网，请点右上角“更新情报”。");
    });
    $("viewNav").addEventListener("click", (event) => {
      const button = event.target.closest("[data-view-nav]");
      if (button) setView(button.dataset.viewNav || "overview");
    });
    $("runBtn").addEventListener("click", async () => {
      showToast("已开始更新情报，后台正在抓取和整理。", true);
      await api("/api/run", { method: "POST" });
      await loadStats();
      await safeLoadRuntimeStatus();
      startRunPolling();
    });
    $("clusters").addEventListener("click", async (event) => {
      const openLink = event.target.closest("a[data-open]");
      if (openLink) {
        await recordEvent(openLink.dataset.open, "open");
        setTimeout(refresh, 800);
        return;
      }
      const button = event.target.closest("button[data-cluster-hash]");
      if (button) await openDetail(button.dataset.clusterHash);
    });
    $("watchRadar").addEventListener("click", async (event) => {
      const openLink = event.target.closest("a[data-open]");
      if (openLink) {
        await recordEvent(openLink.dataset.open, "open");
        setTimeout(refresh, 800);
        return;
      }
      const target = event.target.closest("[data-watch-target]");
      if (target) await openWatchTargetDetail(target.dataset.watchTarget);
    });
    $("watchHistory").addEventListener("click", async (event) => {
      const target = event.target.closest("[data-watch-target]");
      if (target) await openWatchTargetDetail(target.dataset.watchTarget);
    });
    $("items").addEventListener("click", async (event) => {
      const openLink = event.target.closest("a[data-open]");
      if (openLink) {
        await recordEvent(openLink.dataset.open, "open");
        setTimeout(refresh, 800);
        return;
      }
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const card = button.closest(".intel-card");
      const hash = card.dataset.hash;
      if (button.dataset.action === "detail") await openDetail(hash);
      if (button.dataset.action === "later") await toggleMark(hash, { read_status: button.textContent === "取消稍后" ? "unread" : "later" });
      if (button.dataset.action === "archive") await toggleMark(hash, { read_status: button.textContent === "取消归档" ? "unread" : "archived" });
      if (button.dataset.action === "favorite") await toggleMark(hash, { favorite: button.textContent !== "已收藏" });
      if (button.dataset.action === "ignore") await toggleMark(hash, { ignored: button.textContent !== "恢复" });
    });
    $("detailBody").addEventListener("click", async (event) => {
      const related = event.target.closest("button[data-related-hash]");
      if (related) await openDetail(related.dataset.relatedHash);
      const openLink = event.target.closest("a[data-open]");
      if (openLink) {
        await recordEvent(openLink.dataset.open, "open");
        setTimeout(refresh, 800);
      }
    });

    hydrateIcons();
    loadTheme();
    loadDashboardView();
    refresh().catch((error) => {
      $("subtitle").textContent = error.message;
    });
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
