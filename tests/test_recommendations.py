"""analysis._build_recommendations 분기 단위 테스트 (PDF 없이)."""
from __future__ import annotations

from pdf_study import analysis


def _toc(is_cand: bool, entries=None, keyword=True):
    return {
        "has_toc_keyword": keyword,
        "is_candidate": is_cand,
        "entries": entries or [],
    }


def test_no_text_layer_is_rejected():
    r = analysis._build_recommendations(
        page_count=10,
        toc_result=_toc(False),
        text_quality="no_text_layer",
    )
    assert r["rejected"] is True
    assert r["primary_mode"] is None
    assert "ocrmypdf" in r["reason"] or "OCR" in r["reason"]


def test_toc_present_routes_to_from_toc():
    entries = [
        {"title": "1장", "page": 5},
        {"title": "2장", "page": 20},
        {"title": "3장", "page": 40},
    ]
    r = analysis._build_recommendations(
        page_count=80,
        toc_result=_toc(True, entries),
        text_quality="high",
    )
    assert r["primary_mode"] == "from_toc"
    chs = r["suggested_chapters"]
    assert len(chs) == 3
    assert chs[0]["page_range"] == [5, 19]
    assert chs[1]["page_range"] == [20, 39]
    assert chs[-1]["page_range"][1] == 80


def test_short_pdf_routes_to_single_unit():
    r = analysis._build_recommendations(
        page_count=30,
        toc_result=_toc(False),
        text_quality="medium",
    )
    assert r["primary_mode"] == "single_unit"
    assert r["suggested_chapters"] == [
        {"chapter_id": "ch1", "title": "전체", "page_range": [1, 30]}
    ]


def test_large_pdf_routes_to_chunks():
    r = analysis._build_recommendations(
        page_count=300,
        toc_result=_toc(False),
        text_quality="medium",
    )
    assert r["primary_mode"] == "chunks"
    chs = r["suggested_chapters"]
    assert len(chs) == 15  # 300 / 20
    assert chs[0]["page_range"] == [1, 20]


def test_medium_pdf_no_toc_routes_to_ask_user():
    r = analysis._build_recommendations(
        page_count=80,
        toc_result=_toc(False),
        text_quality="medium",
    )
    assert r["primary_mode"] == "ask_user"
    # chunks fallback이 suggested에 미리 들어 있어야 메인 LLM이 바로 쓸 수 있음
    assert r["suggested_chapters"]
