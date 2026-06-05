"""analysis.scan_pdf_impl + set_chapters_impl E2E 테스트 (fixture PDF)."""
from __future__ import annotations

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


def test_scan_pdf_ko_with_toc_routes_to_from_toc(make_workspace, ko_with_toc):
    wid, _ = make_workspace(ko_with_toc)
    out = analysis.scan_pdf_impl(wid)
    assert out["language"] == "ko"
    assert out["recommendations"]["primary_mode"] == "from_toc"
    chs = out["recommendations"]["suggested_chapters"]
    titles = [c["title"] for c in chs]
    # 본문 챕터 3장은 무조건 있어야 한다.
    for needed in ("제1장 트랜잭션", "제2장 인덱싱", "제3장 분산 시스템"):
        assert any(needed in t for t in titles), titles
    # state.language도 ko로 저장
    assert workspace.load_state(wid)["language"] == "ko"
    assert workspace.load_state(wid)["phases"]["scanning"] == "completed"


def test_scan_pdf_short_routes_to_single_unit(make_workspace, ko_short):
    wid, _ = make_workspace(ko_short)
    out = analysis.scan_pdf_impl(wid)
    assert out["language"] == "ko"
    assert out["recommendations"]["primary_mode"] == "single_unit"
    ch = out["recommendations"]["suggested_chapters"][0]
    assert ch["chapter_id"] == "ch1"
    assert ch["title"] == "전체"
    assert ch["page_range"] == [1, 12]
    assert "printed_range" in ch  # offset 메타 부착 확인


def test_scan_pdf_scanned_empty_is_rejected(make_workspace, scanned_empty):
    wid, _ = make_workspace(scanned_empty)
    out = analysis.scan_pdf_impl(wid)
    assert out["text_quality"] == "no_text_layer"
    assert out["recommendations"]["rejected"] is True


def test_scan_pdf_ocr_mode_renders_pages_and_bypasses_rejection(
    make_workspace, scanned_empty
):
    """OCR 모드: 텍스트 레이어 없는 PDF도 거부하지 않고 scan_page_images를 렌더."""
    wid, _ = make_workspace(scanned_empty, extraction_mode="ocr")
    out = analysis.scan_pdf_impl(wid)
    assert out["extraction_mode"] == "ocr"
    assert out["recommendations"]["rejected"] is False  # 품질 거부 우회
    assert out["scan_page_images"], "첫 N페이지가 이미지로 렌더되어야 함"
    from pathlib import Path
    assert Path(out["scan_page_images"][0]["path"]).exists()
    # OCR 안내가 next_step_guidance에 포함
    assert "[OCR 모드]" in out["recommendations"]["next_step_guidance"]


def test_scan_pdf_ocr_mode_ignores_text_toc(make_workspace, ko_with_toc):
    """OCR 모드는 스크립트 목차 파싱을 건너뛰고(메인 에이전트가 이미지로 분석),
    서버는 챕터를 제안하지 않는다(suggested_chapters 비움 + chunk_fallback 분리)."""
    wid, _ = make_workspace(ko_with_toc, extraction_mode="ocr")
    out = analysis.scan_pdf_impl(wid)
    rec = out["recommendations"]
    # 서버가 챕터를 제안하지 않음 — 에이전트가 이미지로 직접 분석
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert rec["suggested_chapters"] == []
    # 청크는 최후 수단으로 chunk_fallback에 분리 (suggested로 새지 않음)
    assert rec["chunk_fallback"], "목차 못 읽을 때 쓸 청크 fallback은 있어야 함"
    assert out["scan_page_images"]
    # toc_finder를 돌리지 않음 — toc_candidates는 빈 후보 + note
    toc = out["toc_candidates"]
    assert toc["is_candidate"] is False
    assert toc["entries"] == []
    assert "메인 에이전트" in toc.get("note", "")


def test_set_chapters_extracts_text(make_workspace, ko_with_toc):
    wid, _ = make_workspace(ko_with_toc)
    scan = analysis.scan_pdf_impl(wid)
    chs = scan["recommendations"]["suggested_chapters"]
    res = analysis.set_chapters_impl(wid, chs, {"title": "테스트용 한국어 책"})

    # 모든 챕터에 텍스트가 추출되고
    for c in res["chapters"]:
        assert c["error"] is None, c
        assert c["char_count"] > 0
    # 그림 기능은 제거됨 — 추출 결과에 이미지 관련 필드가 없어야 한다
    assert "total_images" not in res
    assert all("image_count" not in c for c in res["chapters"])

    # state.chapters의 char_count 갱신
    state = workspace.load_state(wid)
    for cid in [c["chapter_id"] for c in res["chapters"]]:
        assert state["chapters"][cid]["char_count"] > 0


def test_ocr_mode_set_chapters_and_get_content(make_workspace, ko_with_toc):
    """OCR 모드: set_chapters는 본문 텍스트를 안 뽑고, get_chapter_content가
    페이지 이미지를 렌더해 page_images로 돌려준다. language도 state에 반영."""
    wid, _ = make_workspace(ko_with_toc, extraction_mode="ocr")
    analysis.scan_pdf_impl(wid)
    res = analysis.set_chapters_impl(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 6]}],
        {"title": "테스트용 한국어 책"},
        language="ko",
    )
    assert res["chapters"][0]["char_count"] == 0  # 텍스트 미추출
    assert workspace.load_state(wid)["language"] == "ko"

    # raw에는 text 필드가 없어야 한다
    raw = workspace.get_chapter_raw(wid, "ch1")
    assert "text" not in raw

    # get_chapter_content_impl이 페이지 이미지를 렌더해 채운다
    content = analysis.get_chapter_content_impl(wid, "ch1")
    pages = content["page_images"]
    assert [p["page"] for p in pages] == [1, 2, 3, 4, 5, 6]
    from pathlib import Path
    assert all(Path(p["path"]).exists() for p in pages)


def test_get_chapter_content_rejects_unregistered_id_with_hint(make_workspace, ko_short):
    """등록 안 된 chapter_id('p11-p18' 같은 페이지범위)는 유효 id 목록 + 안내로 거부.

    OCR 흐름에서 에이전트가 페이지 이미지 id를 chapter_id로 착각해 호출하던 버그.
    """
    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid)
    analysis.set_chapters_impl(wid, [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 12]},
    ])
    with pytest.raises(FileNotFoundError) as ei:
        analysis.get_chapter_content_impl(wid, "p11-p18")
    msg = str(ei.value)
    assert "p11-p18" in msg
    assert "ch1" in msg                 # 유효 chapter_id 안내
    assert "scan_page_images" in msg    # 페이지 직접 보기 안내


def test_set_chapters_rejects_out_of_range(make_workspace, ko_short):
    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid)
    with pytest.raises(ValueError, match="invalid for"):
        analysis.set_chapters_impl(wid, [
            {"chapter_id": "ch1", "title": "t", "page_range": [1, 999]}
        ])


def test_set_chapters_rejects_duplicate_ids(make_workspace, ko_short):
    wid, _ = make_workspace(ko_short)
    analysis.scan_pdf_impl(wid)
    with pytest.raises(ValueError, match="duplicate"):
        analysis.set_chapters_impl(wid, [
            {"chapter_id": "ch1", "title": "a", "page_range": [1, 5]},
            {"chapter_id": "ch1", "title": "b", "page_range": [6, 10]},
        ])


def test_set_chapters_requires_scan_first(make_workspace, ko_short):
    wid, _ = make_workspace(ko_short)
    with pytest.raises(RuntimeError, match="scan_pdf"):
        analysis.set_chapters_impl(wid, [
            {"chapter_id": "ch1", "title": "t", "page_range": [1, 5]}
        ])
