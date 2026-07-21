"""챕터 분할 + 챕터별 텍스트 추출.

페이지 인덱스는 1-based inclusive. 0-based 변환은 reader 모듈에서 수행.
"""
from __future__ import annotations

from typing import Any

import fitz

from . import reader

DEFAULT_CHUNK_SIZE = 20


def make_chunks(page_count: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[dict[str, Any]]:
    """목차가 없을 때 균등 분할용 챕터 fallback 생성.

    Args:
        page_count: 전체 페이지 수 (1-based 컨벤션의 끝값)
        chunk_size: 청크당 페이지 수

    Returns:
        [{"chapter_id": "ch1", "title": "...", "pdf_pages": [s, e]}, ...]
        pdf_pages는 1-based inclusive.
    """
    if page_count < 1:
        return []
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

    chapters = []
    idx = 1
    start = 1
    while start <= page_count:
        end = min(start + chunk_size - 1, page_count)
        chapters.append({
            "chapter_id": f"ch{idx}",
            "title": f"Part {idx} (p.{start}-{end})",
            "pdf_pages": [start, end],
        })
        idx += 1
        start = end + 1
    return chapters


def extract_chapter(doc: fitz.Document, chapter_def: dict[str, Any]) -> dict[str, Any]:
    """챕터 정의에서 본문 텍스트를 추출.

    Args:
        doc: open된 fitz.Document
        chapter_def: {"chapter_id", "title", "pdf_pages": [s, e]} (1-based inclusive)

    Returns:
        {
            "chapter_id": str,
            "title": str,
            "pdf_pages": [s, e],
            "text": str,
            "char_count": int,
        }
    """
    chapter_id = chapter_def["chapter_id"]
    title = chapter_def["title"]
    pdf_pages = chapter_def["pdf_pages"]
    if not (isinstance(pdf_pages, (list, tuple)) and len(pdf_pages) == 2):
        raise ValueError(f"chapter {chapter_id}: pdf_pages must be [start, end]")

    start, end = int(pdf_pages[0]), int(pdf_pages[1])
    text = reader.extract_text_range(doc, start, end)

    return {
        "chapter_id": chapter_id,
        "title": title,
        "pdf_pages": [start, end],
        "text": text,
        "char_count": len(text),
    }
