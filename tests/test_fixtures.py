"""PDF fixture 생성 상태 관리 테스트."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pdf_learner.tests.fixtures import build_fixtures


_FIXTURE_NAMES = ("ko_with_toc.pdf", "ko_short.pdf", "scanned_empty.pdf")


def _write_manifest(directory: Path, fingerprint: dict[str, str]) -> None:
    files = {}
    for name in _FIXTURE_NAMES:
        path = directory / name
        path.write_bytes(name.encode())
        files[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (directory / ".fixture-manifest.json").write_text(
        json.dumps({"fingerprint": fingerprint, "files": files}),
        encoding="utf-8",
    )


def test_current_fixture_manifest_skips_regeneration(tmp_path, monkeypatch):
    fingerprint = {"generator_sha256": "current"}
    _write_manifest(tmp_path, fingerprint)
    monkeypatch.setattr(build_fixtures, "fixture_fingerprint", lambda: fingerprint)

    def fail_build(*args, **kwargs):
        raise AssertionError("current fixtures should not be regenerated")

    monkeypatch.setattr(build_fixtures, "build_all", fail_build)

    paths = build_fixtures.ensure_fixtures(tmp_path)

    assert set(paths) == {name.removesuffix(".pdf") for name in _FIXTURE_NAMES}


def test_stale_fixture_manifest_regenerates_fixtures(tmp_path, monkeypatch):
    _write_manifest(tmp_path, {"generator_sha256": "old"})
    current = {"generator_sha256": "current"}
    monkeypatch.setattr(build_fixtures, "fixture_fingerprint", lambda: current)
    calls = []

    def fake_build(out_dir=None):
        calls.append(out_dir)
        return {
            name.removesuffix(".pdf"): tmp_path / name
            for name in _FIXTURE_NAMES
        }

    monkeypatch.setattr(build_fixtures, "build_all", fake_build)

    build_fixtures.ensure_fixtures(tmp_path)

    assert calls == [tmp_path]
    manifest = json.loads(
        (tmp_path / ".fixture-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["fingerprint"] == current


def test_modified_fixture_file_regenerates_fixtures(tmp_path, monkeypatch):
    current = {"generator_sha256": "current"}
    _write_manifest(tmp_path, current)
    (tmp_path / "ko_short.pdf").write_bytes(b"modified")
    monkeypatch.setattr(build_fixtures, "fixture_fingerprint", lambda: current)
    calls = []

    def fake_build(out_dir=None):
        calls.append(out_dir)
        return {
            name.removesuffix(".pdf"): tmp_path / name
            for name in _FIXTURE_NAMES
        }

    monkeypatch.setattr(build_fixtures, "build_all", fake_build)

    build_fixtures.ensure_fixtures(tmp_path)

    assert calls == [tmp_path]
