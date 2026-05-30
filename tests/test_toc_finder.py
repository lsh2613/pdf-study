"""toc_finder.find_toc_candidates 단위 테스트."""
from __future__ import annotations

from pdf_study.pdf import toc_finder


def test_dots_pattern_extracts_entries():
    text = (
        "목차\n"
        "제1장 트랜잭션 ............ 5\n"
        "제2장 인덱싱 .............. 13\n"
        "제3장 분산 시스템 ......... 21\n"
    )
    r = toc_finder.find_toc_candidates(text)
    assert r["has_toc_keyword"] is True
    assert r["is_candidate"] is True
    titles = [e["title"] for e in r["entries"]]
    pages = [e["page"] for e in r["entries"]]
    assert titles == ["제1장 트랜잭션", "제2장 인덱싱", "제3장 분산 시스템"]
    assert pages == [5, 13, 21]


def test_wide_space_pattern():
    text = (
        "Contents\n"
        "Introduction    3\n"
        "Methods         12\n"
        "Conclusion      40\n"
    )
    r = toc_finder.find_toc_candidates(text)
    assert r["is_candidate"]
    assert r["entries"][0]["page"] == 3
    assert r["entries"][-1]["page"] == 40


def test_paren_pattern():
    text = (
        "차례\n"
        "서론 (5)\n"
        "본론 (12)\n"
        "결론 (30)\n"
    )
    r = toc_finder.find_toc_candidates(text)
    assert r["is_candidate"]
    assert [e["page"] for e in r["entries"]] == [5, 12, 30]


def test_below_min_entries_not_candidate():
    """MIN_TOC_ENTRIES = 3 미만은 후보 아님."""
    text = "목차\n제1장 ... 5\n제2장 ... 10\n"
    r = toc_finder.find_toc_candidates(text)
    assert r["is_candidate"] is False
    assert len(r["entries"]) == 2


def test_monotonic_filter_removes_noise():
    """페이지 번호가 감소하는 노이즈 라인은 LIS로 걸러져야 한다."""
    text = (
        "목차\n"
        "제1장 도입 ........ 5\n"
        "노이즈 라인 ...... 99\n"
        "제2장 본론 ....... 12\n"   # 99 < 12 이므로 둘 중 하나는 LIS에서 빠짐
        "제3장 결론 ....... 25\n"
    )
    r = toc_finder.find_toc_candidates(text)
    pages = [e["page"] for e in r["entries"]]
    # 단조 비감소
    assert pages == sorted(pages)


def test_keyword_without_entries():
    """키워드는 있지만 패턴 매칭 0건이면 후보 아님."""
    text = "목차\n잡담 문단 한 줄.\n"
    r = toc_finder.find_toc_candidates(text)
    assert r["has_toc_keyword"] is True
    assert r["is_candidate"] is False
    assert r["entries"] == []
