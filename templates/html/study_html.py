#!/usr/bin/env python3
"""학습 자료 런처: 정적 파일 서빙 + 진도 read/write API.

사용법:
    python study_html.py [--port 8765] [--no-browser]
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
PROGRESS_DIR = ROOT / "progress"
PROGRESS_DIR.mkdir(exist_ok=True)

SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
DEFAULT_PORT = 8765


class StudyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # --------- routing ---------
    def do_GET(self):
        if self.path.startswith("/api/progress/"):
            self._handle_progress_get()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/progress/"):
            self._handle_progress_post()
        else:
            self.send_error(404)

    # --------- helpers ---------
    def _progress_file(self):
        key = self.path.rsplit("/", 1)[-1]
        if key == "global":
            return PROGRESS_DIR / "_global.json"
        if SAFE_NAME.match(key):
            return PROGRESS_DIR / f"{key}.json"
        return None

    def _handle_progress_get(self):
        f = self._progress_file()
        if f is None:
            return self.send_error(400)
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        self._send_json(data)

    def _handle_progress_post(self):
        f = self._progress_file()
        if f is None:
            return self.send_error(400)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            json.loads(body)  # 잘못된 JSON은 거부
        except json.JSONDecodeError:
            return self.send_error(400)
        f.write_text(body, encoding="utf-8")
        self._send_json({"ok": True})

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    # 진입 페이지: index.html 우선, 없으면 main.html
    entry = "index.html" if (ROOT / "index.html").exists() else "main.html"
    server = HTTPServer(("127.0.0.1", args.port), StudyHandler)
    actual_port = server.server_port
    url = f"http://127.0.0.1:{actual_port}/{entry}"
    print(f"Study server running at {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
