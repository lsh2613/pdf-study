"""HTML과 Markdown+TUI가 공유하는 챕터 페이지 표기."""
from __future__ import annotations

from typing import Any


def format_page_label(
    meta: dict[str, Any],
    *,
    page_offset: int | None,
) -> str:
    """PDF 페이지와 선택적 원문 페이지를 사람이 읽는 한 줄로 만든다."""
    pdf_pages = meta.get("pdf_pages", meta.get("page_range"))
    if not isinstance(pdf_pages, (list, tuple)) or len(pdf_pages) != 2:
        return ""

    label = f"PDF p.{pdf_pages[0]}–{pdf_pages[1]}"
    source_present = "source_pages" in meta or "printed_range" in meta
    if not source_present:
        return label

    source_pages = meta.get("source_pages", meta.get("printed_range"))
    if isinstance(source_pages, (list, tuple)) and len(source_pages) == 2:
        return f"{label} · 원문 p.{source_pages[0]}–{source_pages[1]}"
    if source_pages is None:
        suffix = "원문 페이지 미상" if page_offset is None else "원문 페이지 없음"
        return f"{label} · {suffix}"
    return label
