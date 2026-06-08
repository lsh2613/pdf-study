"""analysis.scan_pdf_impl + set_chapters_impl E2E 테스트 (fixture PDF)."""
from __future__ import annotations

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


def test_ocr_mode_set_chapters_and_get_content(make_workspace, ko_with_toc):
    """extraction_mode="ocr": set_chapters는 본문 텍스트를 안 뽑고,
    get_chapter_content가 페이지 이미지를 렌더해 page_images로 돌려준다."""
    wid, _ = make_workspace(ko_with_toc)
    analysis.scan_pdf_impl(wid)
    res = analysis.set_chapters_impl(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 6]}],
        "sequential", "ocr",
        book_info={"title": "테스트용 한국어 책"},
        language="ko",
    )
    assert res["chapters"][0]["char_count"] == 0  # 텍스트 미추출
    state = workspace.load_state(wid)
    assert state["language"] == "ko"
    assert state["extraction_mode"] == "ocr"

    # raw에는 text 필드가 없어야 한다
    raw = workspace.get_chapter_raw(wid, "ch1")
    assert "text" not in raw

    # get_chapter_content_impl이 페이지 이미지를 렌더해 채운다
    content = analysis.get_chapter_content_impl(wid, "ch1")
    pages = content["page_images"]
    assert [p["page"] for p in pages] == [1, 2, 3, 4, 5, 6]
    assert all(Path(p["path"]).exists() for p in pages)


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
