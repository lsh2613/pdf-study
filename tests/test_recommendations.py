"""analysis._build_recommendations + _outline_to_chapters 단위 테스트 (PDF 없이)."""
from __future__ import annotations

from pdf_study import analysis


def _outline_chs():
    return [
        {"chapter_id": "ch1", "title": "1장", "page_range": [5, 19]},
        {"chapter_id": "ch2", "title": "2장", "page_range": [20, 39]},
        {"chapter_id": "ch3", "title": "3장", "page_range": [40, 80]},
    ]


# ---------------------------------------------------------------------------
# _build_recommendations
# ---------------------------------------------------------------------------

def test_outline_present_routes_to_from_outline():
    r = analysis._build_recommendations(page_count=80, outline_chapters=_outline_chs())
    assert r["rejected"] is False
    assert r["primary_mode"] == "from_outline"
    assert [c["title"] for c in r["suggested_chapters"]] == ["1장", "2장", "3장"]
    # outline 경로는 vision 재분석을 선택지에 포함 (틀리면 force_vision)
    assert r["user_choices"] == [
        "proceed", "reanalyze_with_vision", "manual_pdf_pages", "chunks",
    ]
    assert "force_vision=True" in r["next_step_guidance"]


def test_no_outline_routes_to_vision():
    r = analysis._build_recommendations(page_count=120, outline_chapters=None)
    assert r["rejected"] is False
    assert r["primary_mode"] == "analyze_toc_from_images"
    assert r["suggested_chapters"] == []
    assert r["chunk_fallback"]               # 최후 수단 청크는 분리 제공
    assert r["user_choices"] == ["proceed", "manual_pdf_pages", "chunks"]
    g = r["next_step_guidance"]
    # 텍스트/스크립트 추정 금지 + 이미지 직독 강제
    assert "스크립트" in g and "toc_page_images" in g


def test_no_outline_does_not_reject_low_quality():
    # 텍스트 품질과 무관 — 거부 없음(스캔본도 vision으로)
    r = analysis._build_recommendations(page_count=10, outline_chapters=None)
    assert r.get("rejected") in (False, None)
    assert r["primary_mode"] == "analyze_toc_from_images"


def test_outline_offset_annotates_printed_range():
    # 물리 5/20/40, offset 18 → front matter는 클램프, 본문은 책 페이지로 변환
    r = analysis._build_recommendations(
        page_count=80, outline_chapters=_outline_chs(),
        page_offset=18, offset_confidence="high",
    )
    chs = r["suggested_chapters"]
    # ch1 물리[5,19] → 책[-13,1] → 끝≥1이라 시작만 1로 클램프
    assert chs[0]["printed_range"] == [1, 1]
    assert chs[1]["printed_range"] == [2, 21]
    assert r["page_offset"] == 18
    assert r["offset_confidence"] == "high"
    assert r["physical_range"] == [1, 80]
    assert r["printed_range_available"] == [1, 62]   # 80 - 18


def test_no_offset_marks_printed_range_none():
    r = analysis._build_recommendations(page_count=80, outline_chapters=_outline_chs())
    assert r["page_offset"] is None
    assert r["offset_confidence"] == "none"
    assert all(c["printed_range"] is None for c in r["suggested_chapters"])
    assert r["printed_range_available"] is None


# ---------------------------------------------------------------------------
# _outline_to_chapters
# ---------------------------------------------------------------------------

def test_outline_to_chapters_top_level_only_and_ranges():
    outline = [
        {"level": 1, "title": "1장", "page": 5},
        {"level": 2, "title": "1.1 절", "page": 6},   # 하위 절 → 제외
        {"level": 1, "title": "2장", "page": 13},
        {"level": 1, "title": "3장", "page": 21},
    ]
    chs = analysis._outline_to_chapters(outline, page_count=28)
    assert [c["title"] for c in chs] == ["1장", "2장", "3장"]
    assert chs[0]["page_range"] == [5, 12]
    assert chs[1]["page_range"] == [13, 20]
    assert chs[2]["page_range"] == [21, 28]   # 마지막 끝 = page_count


def test_outline_to_chapters_drops_beyond_excerpt():
    outline = [
        {"level": 1, "title": "1장", "page": 5},
        {"level": 1, "title": "2장", "page": 13},
        {"level": 1, "title": "4장", "page": 200},  # page_count 초과 → 드롭
    ]
    chs = analysis._outline_to_chapters(outline, page_count=28)
    assert [c["title"] for c in chs] == ["1장", "2장"]
    assert chs[-1]["page_range"] == [13, 28]


def test_outline_to_chapters_empty():
    assert analysis._outline_to_chapters([], page_count=10) == []
