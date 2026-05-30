"""pdf.images 테스트 — 풀페이지 필터 + 작은 아이콘 필터 검증."""
from __future__ import annotations

from pdf_study.pdf import images, reader


def test_full_page_raster_filtered_out(scanned_empty, tmp_path):
    """scanned_empty.pdf는 페이지가 통째로 raster — 0장 추출."""
    d = reader.open_pdf(scanned_empty)
    try:
        refs = images.extract_chapter_images(d, "ch1", [1, 5], tmp_path / "imgs")
    finally:
        d.close()
    assert refs == []


def test_body_figures_pass_full_page_filter(ko_with_toc, tmp_path):
    """ko_with_toc.pdf의 각 챕터에는 본문 그림이 한 장씩 있고
    풀페이지 배경/작은 아이콘이 함께 있다 — 본문 그림만 통과해야."""
    d = reader.open_pdf(ko_with_toc)
    try:
        refs = images.extract_chapter_images(d, "ch1", [5, 12], tmp_path / "imgs")
    finally:
        d.close()
    # 본문 그림 1장만 통과 (풀페이지 배경 + 작은 아이콘은 거름)
    assert len(refs) == 1, refs
    assert refs[0]["page"] in range(5, 13)
    from pathlib import Path
    assert Path(refs[0]["path"]).exists()


def test_no_images_for_text_only_synthetic_pdf(tmp_path):
    """이미지가 없는 PDF는 빈 결과."""
    import fitz
    p = tmp_path / "noimg.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=595, height=841)
    doc.save(str(p))
    doc.close()

    d = reader.open_pdf(p)
    try:
        refs = images.extract_chapter_images(d, "ch1", [1, 3], tmp_path / "imgs")
    finally:
        d.close()
    assert refs == []
