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


def test_set_chapters_extracts_text_and_filters_images(make_workspace, ko_with_toc):
    wid, _ = make_workspace(ko_with_toc)
    scan = analysis.scan_pdf_impl(wid)
    chs = scan["recommendations"]["suggested_chapters"]
    res = analysis.set_chapters_impl(wid, chs, {"title": "테스트용 한국어 책"})

    # 모든 챕터에 텍스트가 추출되고
    for c in res["chapters"]:
        assert c["error"] is None, c
        assert c["char_count"] > 0
    # 본문 그림 3장(각 챕터 1장) + 찾아보기는 그림 없음 = 총 3장 (이미지 필터 작동)
    assert res["total_images"] == 3

    # state.chapters의 char_count 갱신
    state = workspace.load_state(wid)
    for cid in [c["chapter_id"] for c in res["chapters"]]:
        assert state["chapters"][cid]["char_count"] > 0


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
