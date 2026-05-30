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
