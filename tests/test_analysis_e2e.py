"""analysis.scan_pdf_impl + set_chapters_impl E2E 테스트 (fixture PDF)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from pdf_study import analysis, workspace


@pytest.fixture
def make_workspace(tmp_path):
    """work_id 발급 + register까지 한 줄로."""
    def _make(pdf_path, **opts):
        out = tmp_path / f"out_{pdf_path.stem}"
        wid = workspace.create_workspace(
            pdf_path, out,
            options={"multiple_choice": True, "short_answer": True,
                     "reflection": True, "extension": True},
            **opts,
        )
        return wid, out
    return _make


def test_scan_pdf_ko_with_toc_routes_to_from_outline(make_workspace, ko_with_toc):
    """내장 목차(북마크)가 있으면 from_outline으로 챕터를 구성한다."""
    wid, _ = make_workspace(ko_with_toc)
    out = analysis.scan_pdf_impl(wid)
    assert out["language"] == "ko"
    assert out["outline_present"] is True
    rec = out["recommendations"]
    assert rec["primary_mode"] == "from_outline"
    chs = rec["suggested_chapters"]
    titles = [c["title"] for c in chs]
    for needed in ("제1장 트랜잭션", "제2장 인덱싱", "제3장 분산 시스템"):
        assert any(needed in t for t in titles), titles
    # 북마크 물리 페이지(5/13/21)가 그대로 반영
    assert [c["page_range"][0] for c in chs] == [5, 13, 21]
    # 텍스트는 노출하지 않는다
    assert "scanned_text" not in out
    assert workspace.load_state(wid)["phases"]["scanning"] == "completed"


def test_scan_pdf_no_outline_routes_to_vision(make_workspace, ko_short):
    """내장 목차가 없으면 vision 경로 — 목차 페이지를 렌더해 에이전트가 읽는다."""
    wid, _ = make_workspace(ko_short)
    out = analysis.scan_pdf_impl(wid)
    assert out["outline_present"] is False
    rec = out["recommendations"]
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert rec["suggested_chapters"] == []
    assert rec["chunk_fallback"]
    assert out["toc_page_images"], "목차 페이지가 이미지로 렌더되어야 함"
    assert Path(out["toc_page_images"][0]["path"]).exists()
    assert "scan_page_images" not in out


def test_scan_pdf_scanned_no_text_routes_to_vision_not_rejected(
    make_workspace, scanned_empty
):
    """텍스트 레이어가 없어도 거부하지 않고 vision 경로로 간다."""
    wid, _ = make_workspace(scanned_empty)
    out = analysis.scan_pdf_impl(wid)
    rec = out["recommendations"]
    assert rec.get("rejected") in (False, None)
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert out["toc_page_images"]


def test_force_vision_skips_outline(make_workspace, ko_with_toc):
    """force_vision=True면 내장 목차를 무시하고 목차 페이지를 vision으로 읽는다."""
    wid, _ = make_workspace(ko_with_toc)
    out = analysis.scan_pdf_impl(wid, force_vision=True)
    assert out["outline_present"] is False
    assert out["recommendations"]["primary_mode"] == "analyze_toc_from_images"
    assert out["toc_page_images"]


def test_set_chapters_extracts_text(make_workspace, ko_with_toc):
    wid, _ = make_workspace(ko_with_toc)
    scan = analysis.scan_pdf_impl(wid)
    chs = scan["recommendations"]["suggested_chapters"]
    res = analysis.set_chapters_impl(
        wid, chs, "sequential", "text", book_info={"title": "테스트용 한국어 책"})

    for c in res["chapters"]:
        assert c["error"] is None, c
        assert c["char_count"] > 0
    # 그림 기능은 제거됨
    assert "total_images" not in res
    assert all("image_count" not in c for c in res["chapters"])

    state = workspace.load_state(wid)
    # 모드가 set_chapters 시점에 state에 기록된다
    assert state["execution_mode"] == "sequential"
    assert state["extraction_mode"] == "text"
    for cid in [c["chapter_id"] for c in res["chapters"]]:
        assert state["chapters"][cid]["char_count"] > 0


def test_ocr_mode_set_chapters_precomputes_raw_text(
    make_workspace, ko_with_toc, monkeypatch
):
    """extraction_mode="ocr": set_chapters가 PaddleOCR 결과를 raw text로 저장한다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    calls = []

    class MockWorker:
        def process_image(self, image_path):
            calls.append(Path(image_path).name)
            return f"text from {Path(image_path).stem}"

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    res = analysis.set_chapters_impl(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 3]}],
        "sequential", "ocr",
        book_info={"title": "테스트용 한국어 책"},
        language="ko",
    )
    expected = "text from p1\n\ntext from p2\n\ntext from p3"
    assert res["chapters"][0]["char_count"] == len(expected)
    state = workspace.load_state(wid)
    assert state["language"] == "ko"
    assert state["extraction_mode"] == "ocr"
    assert state["chapters"]["ch1"]["char_count"] == len(expected)

    raw = workspace.get_chapter_raw(wid, "ch1")
    assert raw["text"] == expected
    assert raw["char_count"] == len(expected)
    assert calls == ["p1.jpg", "p2.jpg", "p3.jpg"]

    content = analysis.get_chapter_content_impl(wid, "ch1")
    assert content["text"] == expected
    assert "page_images" not in content


def test_ocr_body_text_backfilled_to_raw(make_workspace, ko_with_toc):
    """OCR: body_text backfill은 기존 raw text를 덮어쓸 수 있다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    class MockWorker:
        def process_image(self, image_path):
            return "초기 OCR 본문"

    from unittest.mock import patch
    with patch("pdf_study.analysis.ocr.get_ocr_worker", return_value=MockWorker()):
        analysis.set_chapters_impl(
            wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 1]}],
            "sequential", "ocr", language="ko",
        )
    raw0 = workspace.get_chapter_raw(wid, "ch1")
    assert raw0["text"] == "초기 OCR 본문"
    assert raw0["char_count"] == len("초기 OCR 본문")

    body = "이미지에서 읽어낸 본문 전체입니다."
    workspace.save_chapter_result(wid, "ch1", {
        "chapter_id": "ch1", "title": "전체",
        "summary": "요약", "key_points": ["p"],
        "body_text": body,
        "questions": {"multiple_choice": [], "short_answer": [], "reflection": []},
    })
    raw1 = workspace.get_chapter_raw(wid, "ch1")
    assert raw1["text"] == body
    assert raw1["char_count"] == len(body)
    assert workspace.load_state(wid)["chapters"]["ch1"]["char_count"] == len(body)
    summ = json.loads(
        (workspace.summaries_dir(wid) / "ch1.json").read_text(encoding="utf-8"))
    assert "body_text" not in summ
    assert summ["summary"] == "요약"


def test_ocr_page_exception_marks_chapter_failed_without_partial_raw(
    make_workspace, ko_with_toc, monkeypatch
):
    """페이지 OCR 예외가 있으면 해당 챕터는 실패하고 partial raw를 저장하지 않는다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)

    class MockWorker:
        def process_image(self, image_path):
            if Path(image_path).name == "p2.jpg":
                raise RuntimeError("boom")
            return "partial"

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    res = analysis.set_chapters_impl(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 2]}],
        "sequential", "ocr", language="ko",
    )
    assert res["chapters"][0]["error"]
    state_entry = workspace.load_state(wid)["chapters"]["ch1"]
    assert state_entry["summary_status"] == "failed"
    assert "boom" in state_entry["error"]
    with pytest.raises(FileNotFoundError):
        workspace.get_chapter_raw(wid, "ch1")


def test_ocr_empty_chapter_marks_failed_without_raw(
    make_workspace, ko_with_toc, monkeypatch
):
    """개별 페이지가 비는 것은 허용하지만 챕터 전체 공백 OCR은 실패한다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)

    class MockWorker:
        def process_image(self, image_path):
            return "   "

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    res = analysis.set_chapters_impl(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 2]}],
        "sequential", "ocr", language="ko",
    )
    assert "empty text" in res["chapters"][0]["error"]
    state_entry = workspace.load_state(wid)["chapters"]["ch1"]
    assert state_entry["summary_status"] == "failed"
    assert "empty text" in state_entry["error"]
    with pytest.raises(FileNotFoundError):
        workspace.get_chapter_raw(wid, "ch1")


def test_ocr_skips_skip_chapters(make_workspace, ko_with_toc, monkeypatch):
    """skip 챕터는 OCR 호출과 raw 저장 대상에서 제외한다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    calls = []

    class MockWorker:
        def process_image(self, image_path):
            calls.append(image_path)
            return "본문"

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    analysis.set_chapters_impl(
        wid,
        [
            {"chapter_id": "ch1", "title": "표지", "page_range": [1, 1], "skip": True},
            {"chapter_id": "ch2", "title": "본문", "page_range": [2, 2]},
        ],
        "sequential", "ocr", language="ko",
    )
    assert len(calls) == 1
    with pytest.raises(FileNotFoundError):
        workspace.get_chapter_raw(wid, "ch1")
    assert workspace.get_chapter_raw(wid, "ch2")["text"] == "본문"


def test_ocr_chapter_parallelism_honors_worker_limit(
    make_workspace, ko_with_toc, monkeypatch
):
    """챕터 단위 OCR 병렬 실행은 calculate_ocr_worker_limit 값을 따른다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    active = 0
    max_active = 0
    lock = threading.Lock()

    class MockWorker:
        def process_image(self, image_path):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return Path(image_path).stem

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    monkeypatch.setattr(analysis.ocr, "calculate_ocr_worker_limit", lambda: 1)
    analysis.set_chapters_impl(
        wid,
        [
            {"chapter_id": "ch1", "title": "A", "page_range": [1, 1]},
            {"chapter_id": "ch2", "title": "B", "page_range": [2, 2]},
        ],
        "parallel", "ocr", language="ko",
    )
    assert max_active == 1


def test_text_mode_body_text_does_not_overwrite_raw(make_workspace, ko_with_toc):
    """text 모드: raw엔 이미 추출 본문이 있으므로 body_text로 덮어쓰지 않는다."""
    wid, _ = make_workspace(ko_with_toc)
    scan = analysis.scan_pdf_impl(wid)
    chs = scan["recommendations"]["suggested_chapters"][:1]
    analysis.set_chapters_impl(wid, chs, "sequential", "text")
    cid = chs[0]["chapter_id"]
    orig = workspace.get_chapter_raw(wid, cid)["text"]
    assert orig  # 추출 본문 존재

    workspace.save_chapter_result(wid, cid, {
        "chapter_id": cid, "summary": "s", "key_points": [],
        "body_text": "AGENT OVERRIDE",
        "questions": {"multiple_choice": [], "short_answer": [], "reflection": []},
    })
    assert workspace.get_chapter_raw(wid, cid)["text"] == orig


def test_get_chapter_content_rejects_unregistered_id_with_hint(
    make_workspace, ko_short
):
    """등록 안 된 chapter_id('p11-p18' 같은 페이지범위)는 유효 id 목록 + 안내로 거부."""
    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid)
    analysis.set_chapters_impl(wid, [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 12]},
    ], "sequential", "text")
    with pytest.raises(FileNotFoundError) as ei:
        analysis.get_chapter_content_impl(wid, "p11-p18")
    msg = str(ei.value)
    assert "p11-p18" in msg
    assert "ch1" in msg                 # 유효 chapter_id 안내
    assert "toc_page_images" in msg     # 페이지 직접 보기 안내


def test_set_chapters_rejects_out_of_range(make_workspace, ko_short):
    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid)
    with pytest.raises(ValueError, match="invalid for"):
        analysis.set_chapters_impl(wid, [
            {"chapter_id": "ch1", "title": "t", "page_range": [1, 999]}
        ], "sequential", "text")


def test_set_chapters_rejects_duplicate_ids(make_workspace, ko_short):
    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid)
    with pytest.raises(ValueError, match="duplicate"):
        analysis.set_chapters_impl(wid, [
            {"chapter_id": "ch1", "title": "a", "page_range": [1, 5]},
            {"chapter_id": "ch1", "title": "b", "page_range": [6, 10]},
        ], "sequential", "text")


def test_set_chapters_requires_scan_first(make_workspace, ko_short):
    wid, _ = make_workspace(ko_short)
    with pytest.raises(RuntimeError, match="scan_pdf"):
        analysis.set_chapters_impl(wid, [
            {"chapter_id": "ch1", "title": "t", "page_range": [1, 5]}
        ], "sequential", "text")
