"""analysis.scan_pdf_impl + set_chapters_impl E2E 테스트 (fixture PDF)."""
from __future__ import annotations

import json
import concurrent.futures
import threading
import time
from pathlib import Path

import pytest

from pdf_study import analysis, workspace


@pytest.fixture(autouse=True)
def stub_toc_ocr(monkeypatch):
    """목차 OCR 테스트가 실제 PaddleOCR 모델을 로드하지 않게 한다."""
    class StubWorker:
        def process_image(self, image_path):
            return f"OCR:{Path(image_path).name}"

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: StubWorker())
    monkeypatch.setattr(analysis.ocr, "models_cached", lambda: True)
    monkeypatch.setattr(
        analysis.ocr,
        "model_cache_status",
        lambda: {"cache_dir": "fake", "models": [], "all_cached": True},
    )


@pytest.fixture(autouse=True)
def reset_chapter_ocr_executor():
    """전역 챕터 OCR executor가 테스트 간 worker limit monkeypatch를 공유하지 않게 한다."""
    analysis.ocr._reset_chapter_executor_for_tests()
    yield
    analysis.ocr._reset_chapter_executor_for_tests()


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


def test_scan_pdf_no_outline_renders_toc_images_without_ocr(make_workspace, ko_short):
    """내장 목차가 없으면 목차 페이지를 렌더하되 OCR은 별도 단계로 남긴다."""
    wid, _ = make_workspace(ko_short)
    out = analysis.scan_pdf_impl(wid)
    assert out["outline_present"] is False
    rec = out["recommendations"]
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert rec["suggested_chapters"] == []
    assert rec["chunk_fallback"]
    assert out["toc_page_images"], "목차 페이지가 이미지로 렌더되어야 함"
    first = out["toc_page_images"][0]
    assert Path(first["path"]).exists()
    assert first["ocr_text"] == ""
    assert first["ocr_error"] is None
    assert first["ocr_status"] == analysis.TOC_OCR_NOT_STARTED
    assert out["toc_ocr"]["status"] == analysis.TOC_OCR_NOT_STARTED
    assert "scan_page_images" not in out


def test_scan_pdf_scanned_no_text_renders_toc_images_not_rejected(
    make_workspace, scanned_empty
):
    """텍스트 레이어가 없어도 거부하지 않고 목차 이미지 경로로 간다."""
    wid, _ = make_workspace(scanned_empty)
    out = analysis.scan_pdf_impl(wid)
    rec = out["recommendations"]
    assert rec.get("rejected") in (False, None)
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert out["toc_page_images"]
    assert all(
        item["ocr_status"] == analysis.TOC_OCR_NOT_STARTED
        for item in out["toc_page_images"]
    )


def test_force_vision_skips_outline(make_workspace, ko_with_toc):
    """force_vision=True면 내장 목차를 무시하고 목차 페이지 이미지 경로로 간다."""
    wid, _ = make_workspace(ko_with_toc)
    out = analysis.scan_pdf_impl(wid, force_vision=True)
    assert out["outline_present"] is False
    assert out["recommendations"]["primary_mode"] == "analyze_toc_from_images"
    assert out["toc_page_images"]
    assert out["toc_page_images"][0]["ocr_status"] == analysis.TOC_OCR_NOT_STARTED


def test_scan_toc_with_ocr_partial_failure_does_not_fail(
    make_workspace, ko_short, monkeypatch
):
    """일부 목차 페이지 OCR 실패는 항목의 ocr_error에만 기록한다."""
    class MixedWorker:
        def process_image(self, image_path):
            if str(image_path).endswith("p2.jpg"):
                raise RuntimeError("p2 OCR failed")
            return "목차\n제1장 시작 3"

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MixedWorker())
    monkeypatch.setattr(analysis.reader, "locate_toc_pages", lambda doc, scan_size: [1, 2])

    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid, scan_size=2)
    out = analysis.scan_toc_with_ocr_impl(wid)

    assert out["recommendations"]["primary_mode"] == "analyze_toc_from_images"
    assert [item["page"] for item in out["toc_page_images"]] == [1, 2]
    assert out["toc_page_images"][0]["ocr_text"] == "목차\n제1장 시작 3"
    assert out["toc_page_images"][0]["ocr_error"] is None
    assert out["toc_page_images"][1]["ocr_text"] == ""
    assert "p2 OCR failed" in out["toc_page_images"][1]["ocr_error"]
    assert out["toc_ocr"]["status"] == "partial_failed"


def test_scan_toc_with_ocr_requires_prepare_when_models_missing(
    make_workspace, ko_short, monkeypatch
):
    monkeypatch.setattr(analysis.ocr, "models_cached", lambda: False)
    monkeypatch.setattr(
        analysis.ocr,
        "model_cache_status",
        lambda: {"cache_dir": "fake", "models": [], "all_cached": False},
    )
    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid)

    out = analysis.scan_toc_with_ocr_impl(wid)

    assert out["requires_prepare_ocr"] is True
    assert out["ocr_cache"]["all_cached"] is False
    assert out["toc_page_images"]


def test_scan_pdf_outline_path_does_not_call_toc_ocr(
    make_workspace, ko_with_toc, monkeypatch
):
    """내장 목차를 쓰는 정상 경로에서는 목차 이미지 렌더/OCR을 하지 않는다."""
    def fail_if_called():
        raise AssertionError("OCR worker should not be called for outline path")

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", fail_if_called)
    wid, _ = make_workspace(ko_with_toc)
    out = analysis.scan_pdf_impl(wid)
    assert out["outline_present"] is True
    assert out["toc_page_images"] == []


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
    assert raw["extraction_mode"] == "ocr"
    assert calls == ["p1.jpg", "p2.jpg", "p3.jpg"]

    content = analysis.get_chapter_content_impl(wid, "ch1")
    assert content["text"] == expected
    assert "page_images" not in content


def test_ocr_mode_reuses_existing_raw_text(
    make_workspace, ko_with_toc, monkeypatch
):
    """현재 page_range와 맞는 OCR raw가 있으면 렌더/OCR을 다시 하지 않는다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    cached_text = "캐시된 OCR 본문"
    workspace.save_chapter_raw(wid, "ch1", {
        "chapter_id": "ch1",
        "title": "이전 제목",
        "page_range": [1, 2],
        "text": cached_text,
        "char_count": len(cached_text),
        "extraction_mode": "ocr",
    })

    def fail_render(*args, **kwargs):
        raise AssertionError("cached OCR raw should avoid page rendering")

    def fail_worker():
        raise AssertionError("cached OCR raw should avoid OCR worker")

    monkeypatch.setattr(analysis.reader, "render_pages", fail_render)
    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", fail_worker)
    res = analysis.set_chapters_impl(
        wid,
        [{"chapter_id": "ch1", "title": "새 제목", "page_range": [1, 2]}],
        "parallel", "ocr", language="ko",
    )

    assert res["failed_chapters"] == []
    assert res["chapters"][0]["char_count"] == len(cached_text)
    raw = workspace.get_chapter_raw(wid, "ch1")
    assert raw["title"] == "새 제목"
    assert raw["text"] == cached_text
    assert raw["extraction_mode"] == "ocr"


def test_ocr_mode_does_not_reuse_text_mode_raw(
    make_workspace, ko_with_toc, monkeypatch
):
    """OCR 모드는 이전 text 모드 raw가 있어도 새 OCR raw로 교체한다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    chapter = {"chapter_id": "ch1", "title": "전체", "page_range": [1, 1]}
    analysis.set_chapters_impl(wid, [chapter], "sequential", "text")
    text_raw = workspace.get_chapter_raw(wid, "ch1")
    assert text_raw["extraction_mode"] == "text"

    class MockWorker:
        def process_image(self, image_path):
            return "OCR 본문"

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    res = analysis.set_chapters_impl(
        wid, [chapter], "sequential", "ocr", language="ko",
    )

    assert res["failed_chapters"] == []
    raw = workspace.get_chapter_raw(wid, "ch1")
    assert raw["text"] == "OCR 본문"
    assert raw["extraction_mode"] == "ocr"


def test_ocr_mode_does_not_promote_legacy_raw_after_failed_retry(
    make_workspace, ko_with_toc, monkeypatch
):
    """모드 메타가 없는 raw는 state가 ocr이어도 OCR 캐시로 승격하지 않는다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    legacy_text = "legacy text raw"
    workspace.save_chapter_raw(wid, "ch1", {
        "chapter_id": "ch1",
        "title": "전체",
        "page_range": [1, 1],
        "text": legacy_text,
        "char_count": len(legacy_text),
    })
    workspace.update_state(wid, extraction_mode="ocr")
    calls = []

    class MockWorker:
        def process_image(self, image_path):
            calls.append(Path(image_path).name)
            return "재시도 OCR 본문"

    monkeypatch.setattr(analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    res = analysis.set_chapters_impl(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 1]}],
        "parallel", "ocr", language="ko",
    )

    assert res["failed_chapters"] == []
    assert calls == ["p1.jpg"]
    raw = workspace.get_chapter_raw(wid, "ch1")
    assert raw["text"] == "재시도 OCR 본문"
    assert raw["extraction_mode"] == "ocr"


def test_ocr_body_text_does_not_overwrite_raw(make_workspace, ko_with_toc):
    """OCR: body_text가 들어와도 set_chapters의 canonical raw를 덮지 않는다."""
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
    assert raw1["text"] == "초기 OCR 본문"
    assert raw1["char_count"] == len("초기 OCR 본문")
    assert workspace.load_state(wid)["chapters"]["ch1"]["char_count"] == len("초기 OCR 본문")
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
    assert res["failed_chapters"][0]["chapter_id"] == "ch1"
    assert res["failed_chapters"][0]["failed_pages"] == [2]
    assert "boom" in res["failed_chapters"][0]["error"]
    state_entry = workspace.load_state(wid)["chapters"]["ch1"]
    assert state_entry["summary_status"] == "failed"
    assert "boom" in state_entry["error"]
    assert state_entry["failed_pages"] == [2]
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
    assert res["failed_chapters"][0]["chapter_id"] == "ch1"
    assert res["failed_chapters"][0]["failed_pages"] == []
    assert "empty text" in res["failed_chapters"][0]["error"]
    state_entry = workspace.load_state(wid)["chapters"]["ch1"]
    assert state_entry["summary_status"] == "failed"
    assert "empty text" in state_entry["error"]
    assert state_entry["failed_pages"] == []
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


def test_ocr_chapter_parallelism_limit_is_global_across_calls(
    make_workspace, ko_with_toc, monkeypatch
):
    """동시 set_chapters 호출끼리도 전역 챕터 OCR 상한을 공유한다."""
    wid1, _ = make_workspace(ko_with_toc)
    wid2, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid1)
    analysis.scan_pdf_impl(wid2)
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
    chapters = [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 1]},
        {"chapter_id": "ch2", "title": "B", "page_range": [2, 2]},
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                analysis.set_chapters_impl,
                wid, chapters, "parallel", "ocr", None, "ko",
            )
            for wid in (wid1, wid2)
        ]
        results = [future.result(timeout=10) for future in futures]

    assert all(result["failed_chapters"] == [] for result in results)
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
