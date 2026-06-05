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


def test_garbled_is_rejected_with_triple_guidance_and_sample():
    r = analysis._build_recommendations(
        page_count=80,
        toc_result=_toc(False),
        text_quality="garbled",
        text_sample="용석눈힎개正딝개mvFtpD갈돌개" * 5,
    )
    assert r["rejected"] is True
    assert r["primary_mode"] is None
    assert r["suggested_chapters"] == []
    # 무손실 재추출(qpdf) + OCR(ocrmypdf) + 그대로 진행(allow_garbled) 세 갈래 안내
    assert "qpdf" in r["reason"]
    assert "ocrmypdf" in r["reason"]
    assert "allow_garbled" in r["reason"]
    # 사용자 확인용 깨진 텍스트 샘플이 실려야 한다
    assert "용석눈힎개" in r["text_sample"]
    assert len(r["text_sample"]) <= analysis.GARBLED_SAMPLE_CHARS


def test_garbled_allow_override_proceeds_with_normal_routing():
    # 사용자가 샘플 확인 후 강행 → 거부하지 않고 페이지 수 기반 라우팅
    r = analysis._build_recommendations(
        page_count=80,
        toc_result=_toc(False),
        text_quality="garbled",
        allow_garbled=True,
    )
    assert r["rejected"] is False
    assert r["primary_mode"] == "ask_user"   # 80p·목차없음 → 기존 흐름 그대로
    assert r["suggested_chapters"]


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


def test_from_toc_applies_offset_printed_to_physical():
    # 목차의 인쇄번호(책 1·6·52)에 offset 18 → 물리 19·24·70 (MySQL 패턴)
    entries = [
        {"title": "01 소개", "page": 1},
        {"title": "02 설치", "page": 6},
        {"title": "03 권한", "page": 52},
    ]
    r = analysis._build_recommendations(
        page_count=93,
        toc_result=_toc(True, entries),
        text_quality="high",
        page_offset=18,
        offset_confidence="high",
    )
    chs = r["suggested_chapters"]
    assert chs[0]["page_range"] == [19, 23]   # 물리
    assert chs[0]["printed_range"] == [1, 5]  # 책
    assert chs[1]["page_range"] == [24, 69]
    assert chs[2]["page_range"] == [70, 93]
    assert chs[2]["printed_range"][0] == 52
    assert r["page_offset"] == 18
    assert r["offset_confidence"] == "high"
    # 발췌본 메타: 이 파일에 실제 존재하는 책 페이지 범위 = [1, 93-18=75]
    assert r["physical_range"] == [1, 93]
    assert r["printed_range_available"] == [1, 75]


def test_from_toc_drops_chapters_beyond_excerpt():
    """발췌본: 목차엔 전체 책 챕터가 다 있어도, 물리 범위를 넘는 챕터는 드롭.

    Real MySQL 발췌본 패턴 — 파일은 책 1~75p(PDF 19~93)만, 목차엔 04장(책 76+)
    이후가 더 적혀 있다. offset 18 기준 물리 시작이 93을 넘는 항목은 제외돼야 함.
    """
    entries = [
        {"title": "01 소개", "page": 1},      # 물리 19  (포함)
        {"title": "02 설치", "page": 6},      # 물리 24  (포함)
        {"title": "03 권한", "page": 52},     # 물리 70  (포함)
        {"title": "04 아키텍처", "page": 76},  # 물리 94 > 93 → 드롭
        {"title": "08 인덱스", "page": 200},   # 물리 218 → 드롭
    ]
    r = analysis._build_recommendations(
        page_count=93,
        toc_result=_toc(True, entries),
        text_quality="high",
        page_offset=18,
        offset_confidence="high",
    )
    chs = r["suggested_chapters"]
    assert len(chs) == 3                       # 04·08 제외
    assert [c["title"] for c in chs] == ["01 소개", "02 설치", "03 권한"]
    assert chs[-1]["page_range"] == [70, 93]   # 마지막 챕터 끝 = page_count
    assert r["printed_range_available"] == [1, 75]


def test_chunks_printed_range_marks_front_matter_none():
    # offset 18: 물리 1-30 청크는 책 번호 < 1 구간(front matter) 포함 → 일부 클램프
    r = analysis._build_recommendations(
        page_count=93,
        toc_result=_toc(False),
        text_quality="high",
        page_offset=18,
        offset_confidence="high",
    )
    first = r["suggested_chapters"][0]      # 물리 [1,30]
    # 책 페이지 = 물리-18 → [-17, 12]; 끝이 ≥1 이라 시작만 1로 클램프
    assert first["page_range"] == [1, 30]
    assert first["printed_range"] == [1, 12]


def test_short_pdf_routes_to_single_unit():
    r = analysis._build_recommendations(
        page_count=30,
        toc_result=_toc(False),
        text_quality="medium",
    )
    assert r["primary_mode"] == "single_unit"
    # offset 미전달(None) → printed_range도 None
    assert r["suggested_chapters"] == [
        {"chapter_id": "ch1", "title": "전체",
         "page_range": [1, 30], "printed_range": None}
    ]
    assert r["page_offset"] is None
    assert r["offset_confidence"] == "none"
    assert r["user_choices"] == ["proceed", "manual_pdf_pages", "chunks"]


def test_large_pdf_routes_to_chunks():
    r = analysis._build_recommendations(
        page_count=300,
        toc_result=_toc(False),
        text_quality="medium",
    )
    assert r["primary_mode"] == "chunks"
    chs = r["suggested_chapters"]
    assert len(chs) == 10  # 300 / 30
    assert chs[0]["page_range"] == [1, 30]


def test_medium_pdf_no_toc_routes_to_ask_user():
    r = analysis._build_recommendations(
        page_count=80,
        toc_result=_toc(False),
        text_quality="medium",
    )
    assert r["primary_mode"] == "ask_user"
    # chunks fallback이 suggested에 미리 들어 있어야 메인 LLM이 바로 쓸 수 있음
    assert r["suggested_chapters"]
