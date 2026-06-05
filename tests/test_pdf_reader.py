"""pdf.reader 테스트 — 실제 한국어 fixture PDF로 검증."""
from __future__ import annotations

import pytest

from pdf_study.pdf import reader


def test_get_pdf_info_extracts_metadata_and_pages(ko_with_toc):
    info = reader.get_pdf_info(ko_with_toc)
    assert info["page_count"] == 28
    assert info["book_metadata"]["title"] == "테스트용 한국어 책"
    assert info["book_metadata"]["author"] == "테스트 저자"


def test_extract_page_text_strips_page_number_lines(ko_with_toc):
    """본문 페이지는 마지막 줄에 페이지 번호만 있고 reader가 제거해야 한다."""
    d = reader.open_pdf(ko_with_toc)
    try:
        # 챕터 본문 페이지 (페이지 번호 라인 "8" 같은 게 들어가 있음)
        t = reader.extract_page_text(d, 8)
        last_line = t.strip().split("\n")[-1]
        # 페이지 번호 패턴(숫자만)만 있는 줄은 제거됨
        assert not last_line.strip().isdigit(), last_line
        # 본문 내용은 남음
        assert "트랜잭션" in t
    finally:
        d.close()


def test_extract_text_range_rejects_invalid_ranges(ko_short):
    d = reader.open_pdf(ko_short)
    try:
        with pytest.raises(ValueError):
            reader.extract_page_text(d, 0)
        with pytest.raises(ValueError):
            reader.extract_page_text(d, d.page_count + 1)
        with pytest.raises(ValueError):
            reader.extract_text_range(d, 5, 3)
    finally:
        d.close()


def test_evaluate_text_quality_classifies_text_layer(ko_with_toc, scanned_empty):
    d1 = reader.open_pdf(ko_with_toc)
    try:
        q1 = reader.evaluate_text_quality(d1)
        assert q1["quality"] in ("medium", "high")
        assert q1["avg_chars_per_page"] > 100
    finally:
        d1.close()

    d2 = reader.open_pdf(scanned_empty)
    try:
        q2 = reader.evaluate_text_quality(d2)
        assert q2["quality"] == "no_text_layer"
        assert q2["avg_chars_per_page"] < 50
    finally:
        d2.close()


def test_get_pdf_info_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        reader.get_pdf_info(tmp_path / "nope.pdf")


# --- 모지바케(인코딩 깨짐) 감지 --------------------------------------------

# ToUnicode 손상 PDF에서 실제로 추출됐던 깨진 텍스트 (일부)
_GARBLED = (
    "임탕테개}씩부눈빙개cJ]…TA&AcJ]…TDT "
    "뜩압읽개正골임숫개돌퀀개,4x!7II!4SIN9A4SI7A솟숫개,4x!7II!4SS, "
    "롤본4,9,,,A잔넓씭IxSAW간본개xNNA€A2m,[[ "
    "狂잔넓씭개…zQzE]]QM]6QJA돌눈쓩뽑개…zQzE]]Qb…zQzE]]QM]6QJ "
    "@]|=Jz▼_pA＜2,2xAE=A뮤섭멀이개넓빌흥 "
    "PJzDp<FA&A|cELzU_<FAzDAH]J<TAE=A/tHz.ddHK "
    "용석눈힎개1)(커개正딝개mvFtpD갈돌개롤넓되"
)

_CLEAN_KO = (
    "트랜잭션은 데이터베이스의 상태를 변화시키기 위해 수행하는 작업의 단위이다. "
    "ACID 속성은 원자성, 일관성, 고립성, 지속성을 의미한다. 분산 시스템의 합의 "
    "알고리즘과 2단계 커밋 프로토콜을 다룬다. 인덱스는 검색 속도를 높여 준다."
)

# 영문 약어가 한글에 붙는 정상 기술 문서 — 오탐(false positive) 방지 확인용
_CLEAN_ACRONYM = (
    "REST API를 사용해 JSON 데이터를 받아 PDF로 변환한다. HTTP 요청은 TCP "
    "위에서 동작하며, OAuth2 토큰을 헤더에 담아 인증한다. AWS EC2에 Docker "
    "이미지를 배포하고 URL과 DNS를 설정한다. TLS 핸드셰이크를 HTTP2로 처리한다."
)

# 숫자·쉼표가 많은 표 형태 정상 텍스트 — 오탐 방지 확인용
_CLEAN_TABLE = (
    "2024년 매출은 1,234,567원이고 2025년은 2,345,678원이다. 증가율은 89.7% "
    "이며, 비용은 12,345원에서 23,456원으로 늘었다. 순이익 3,456,789원을 "
    "기록했고 영업이익률은 12.3%로 집계되었다. 부채는 45,678원 감소했다."
)


def test_mojibake_score_flags_garbled_text():
    assert reader.mojibake_score(_GARBLED) > reader._MOJIBAKE_THRESHOLD


@pytest.mark.parametrize("clean", [_CLEAN_KO, _CLEAN_ACRONYM, _CLEAN_TABLE])
def test_mojibake_score_passes_clean_text(clean):
    # 영문 약어/숫자 표가 섞여도 임계 미만이어야 한다 (오탐 금지)
    assert reader.mojibake_score(clean) < reader._MOJIBAKE_THRESHOLD


def test_mojibake_score_returns_zero_on_short_sample():
    assert reader.mojibake_score("개" * 10) == 0.0


# --- 페이지 오프셋 측정 --------------------------------------------------------

def _pdf_with_footer_numbers(offset, n, blanks=()):
    """물리 i 페이지의 꼬리말에 인쇄번호(i - offset)를 찍은 합성 PDF.

    인쇄번호 < 1(앞 front matter)이거나 blanks면 번호를 찍지 않는다.
    """
    import fitz
    doc = fitz.open()
    for i in range(1, n + 1):
        page = doc.new_page(width=400, height=600)
        if i in blanks:
            continue
        page.insert_text((50, 60), f"본문 페이지 {i} 내용")
        printed = i - offset
        if printed >= 1:
            page.insert_text((180, 560), str(printed))  # 하단 꼬리말
    return doc


def test_detect_page_offset_positive_with_front_matter():
    # offset 3: 물리 1~3은 번호 없음(front matter), 4부터 인쇄번호 1,2,...
    doc = _pdf_with_footer_numbers(offset=3, n=14)
    r = reader.detect_page_offset(doc)
    assert r["offset"] == 3
    assert r["confidence"] == "high"


def test_detect_page_offset_negative_and_blanks():
    # offset -3 (PDF가 책보다 앞섬) + 중간 빈 페이지 → 빈 페이지는 자동 스킵
    doc = _pdf_with_footer_numbers(offset=-3, n=14, blanks=(5, 10))
    r = reader.detect_page_offset(doc)
    assert r["offset"] == -3
    assert r["confidence"] == "high"


def test_detect_page_offset_none_when_no_page_numbers():
    import fitz
    doc = fitz.open()
    for _ in range(6):
        doc.new_page().insert_text((50, 60), "숫자 없는 본문")
    r = reader.detect_page_offset(doc)
    assert r["offset"] is None
    assert r["confidence"] == "none"


# --- 페이지 → JPEG 렌더 (OCR 모드) ------------------------------------------

def test_render_pages_writes_jpeg_per_page(tmp_path):
    import fitz
    doc = fitz.open()
    for i in range(1, 5):
        doc.new_page(width=400, height=600).insert_text((50, 60), f"page {i}")
    refs = reader.render_pages(doc, 2, 4, tmp_path)
    assert [r["page"] for r in refs] == [2, 3, 4]
    for r in refs:
        p = __import__("pathlib").Path(r["path"])
        assert p.exists() and p.suffix == ".jpg" and p.stat().st_size > 0
        assert p.read_bytes()[:3] == b"\xff\xd8\xff"  # JPEG 매직


def test_render_pages_caches_existing(tmp_path):
    import fitz
    doc = fitz.open()
    doc.new_page(width=400, height=600).insert_text((50, 60), "page 1")
    first = reader.render_pages(doc, 1, 1, tmp_path)[0]
    mtime = __import__("pathlib").Path(first["path"]).stat().st_mtime_ns
    again = reader.render_pages(doc, 1, 1, tmp_path)[0]
    # 이미 존재하면 재생성하지 않음 (mtime 불변)
    assert __import__("pathlib").Path(again["path"]).stat().st_mtime_ns == mtime


def test_render_pages_rejects_bad_range(tmp_path):
    import fitz
    doc = fitz.open()
    doc.new_page()
    with pytest.raises(ValueError):
        reader.render_pages(doc, 1, 5, tmp_path)
