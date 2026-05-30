"""학습 자료의 serve.py launcher 테스트 — progress API 라운드트립."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# templates/html/serve.py를 임시 디렉토리에 복사해 실행 — output_dir 가짜로 만든다
SERVE_PY = (
    Path(__file__).resolve().parent.parent / "templates" / "html" / "serve.py"
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(port: int, path: str = "/", timeout: float = 4.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=0.5).read()
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("serve.py did not become ready in time")


@pytest.fixture
def serve_dir(tmp_path):
    """serve.py가 동작할 디렉토리 구성. (main.html 한 장만)"""
    d = tmp_path / "site"
    d.mkdir()
    shutil.copy(SERVE_PY, d / "serve.py")
    (d / "main.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    return d


@pytest.fixture
def serve_proc(serve_dir):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(serve_dir / "serve.py"), "--port", str(port), "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(serve_dir),
    )
    try:
        _wait_ready(port, "/main.html")
        yield port, serve_dir
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _get_json(port, key):
    with urllib.request.urlopen(f"http://localhost:{port}/api/progress/{key}") as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(port, key, payload):
    req = urllib.request.Request(
        f"http://localhost:{port}/api/progress/{key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req).read()


def test_progress_get_returns_null_when_missing(serve_proc):
    port, _ = serve_proc
    assert _get_json(port, "global") is None


def test_progress_round_trip_global(serve_proc):
    port, d = serve_proc
    payload = {"last_chapter": "ch2", "last_position": "summary"}
    _post_json(port, "global", payload)
    assert _get_json(port, "global") == payload
    assert (d / "progress" / "_global.json").exists()


def test_progress_round_trip_chapter(serve_proc):
    port, _ = serve_proc
    payload = {
        "chapter_id": "ch1", "completed": True,
        "answers": {"mc1": {"selected": 0, "correct": True}},
    }
    _post_json(port, "ch1", payload)
    got = _get_json(port, "ch1")
    assert got["completed"] is True
    assert got["answers"]["mc1"]["correct"] is True


def test_progress_rejects_unsafe_name(serve_proc):
    port, _ = serve_proc
    # SAFE_NAME = ^[a-zA-Z0-9_-]+$ — 점 포함은 거부
    try:
        urllib.request.urlopen(f"http://localhost:{port}/api/progress/bad.name")
        raise AssertionError("should be rejected")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_progress_rejects_invalid_json(serve_proc):
    port, _ = serve_proc
    req = urllib.request.Request(
        f"http://localhost:{port}/api/progress/ch1",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        raise AssertionError("invalid JSON should be rejected")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_serves_static_files(serve_proc):
    port, _ = serve_proc
    with urllib.request.urlopen(f"http://localhost:{port}/main.html") as r:
        assert r.status == 200
        assert b"ok" in r.read()
