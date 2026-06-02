"""PyMuPDF 래퍼: PDF 열기, 메타/페이지 텍스트 추출, 품질 평가.

페이지 인덱스 컨벤션:
    외부 API(이 모듈의 공개 함수 인자/반환값)는 모두 **1-based**.
    PyMuPDF는 내부적으로 0-based이며, 변환은 이 모듈 안에서만 수행한다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


_BROKEN_UNICODE_RE = re.compile(r"�+")
_MULTI_WS_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_PAGE_NUM_LINE_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")


def _clean_text(raw: str) -> str:
    """기계적 노이즈 1차 정리: 깨진 유니코드, 반복 공백, 페이지 번호 줄."""
    if not raw:
        return ""
    text = _BROKEN_UNICODE_RE.sub("", raw)
    # 줄 단위로 페이지 번호만 있는 줄 제거
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if _PAGE_NUM_LINE_RE.match(stripped):
            continue
        # 줄 내부 공백 정규화
        lines.append(_MULTI_WS_RE.sub(" ", line.rstrip()))
    text = "\n".join(lines)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def open_pdf(pdf_path: str | Path) -> fitz.Document:
    """PDF 파일을 연다. 호출자가 close() 책임."""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    return fitz.open(str(path))


_METADATA_KEYS = ("title", "author", "subject", "creator", "producer")


def extract_metadata(doc: fitz.Document) -> dict[str, str]:
    """이미 열린 문서에서 내장 메타데이터를 정규화해 추출.

    이미 doc을 들고 있는 호출자가 PDF를 재차 열지 않도록 분리한 헬퍼.
    """
    meta_raw = doc.metadata or {}
    return {k: (meta_raw.get(k) or "").strip() for k in _METADATA_KEYS}


def get_pdf_info(pdf_path: str | Path) -> dict[str, Any]:
    """PDF 기본 정보 + 내장 메타데이터 추출.

    Returns:
        {
            "pdf_path": str,
            "page_count": int,
            "book_metadata": {title, author, subject, creator, producer},
        }
    """
    doc = open_pdf(pdf_path)
    try:
        return {
            "pdf_path": str(pdf_path),
            "page_count": doc.page_count,
            "book_metadata": extract_metadata(doc),
        }
    finally:
        doc.close()


def extract_page_text(doc: fitz.Document, page_number: int) -> str:
    """1-based 페이지 번호로 텍스트 추출 + 노이즈 정리.

    Args:
        doc: open_pdf로 연 문서
        page_number: 1-based 페이지 번호
    """
    if page_number < 1 or page_number > doc.page_count:
        raise ValueError(
            f"page_number {page_number} out of range [1, {doc.page_count}]"
        )
    page = doc.load_page(page_number - 1)  # 경계 변환
    raw = page.get_text("text")
    return _clean_text(raw)


def evaluate_text_quality(doc: fitz.Document, sample_size: int = 20) -> dict[str, Any]:
    """텍스트 레이어 품질 평가.

    Returns:
        {
            "quality": "high" | "medium" | "low" | "no_text_layer",
            "avg_chars_per_page": float,
            "sampled_pages": int,
        }

    임계값:
        no_text_layer: avg < 50  (OCR 권장)
        low:           50 <= avg < 300
        medium:        300 <= avg < 800
        high:          avg >= 800
    """
    n = min(sample_size, doc.page_count)
    if n == 0:
        return {"quality": "no_text_layer", "avg_chars_per_page": 0.0, "sampled_pages": 0}

    # 처음 / 중간 / 끝 부근을 고루 샘플링
    if doc.page_count <= sample_size:
        sample_indices = list(range(doc.page_count))
    else:
        third = sample_size // 3
        rest = sample_size - third * 2
        head = list(range(third))
        mid_start = doc.page_count // 2 - third // 2
        middle = list(range(mid_start, mid_start + third))
        tail = list(range(doc.page_count - rest, doc.page_count))
        sample_indices = sorted(set(head + middle + tail))

    total_chars = 0
    for idx in sample_indices:
        try:
            text = _clean_text(doc.load_page(idx).get_text("text"))
            total_chars += len(text)
        except Exception as e:
            logger.warning("page %d text extraction failed: %s", idx, e)

    avg = total_chars / len(sample_indices)
    if avg < 50:
        quality = "no_text_layer"
    elif avg < 300:
        quality = "low"
    elif avg < 800:
        quality = "medium"
    else:
        quality = "high"

    return {
        "quality": quality,
        "avg_chars_per_page": round(avg, 1),
        "sampled_pages": len(sample_indices),
    }


def extract_text_range(doc: fitz.Document, start_page: int, end_page: int) -> str:
    """1-based inclusive 범위의 텍스트를 합쳐서 반환.

    페이지 구분은 빈 줄 + 페이지 헤더 없이 그냥 \\n\\n.
    호출자가 페이지별 처리를 원하면 extract_page_text를 직접 사용.
    """
    if start_page < 1 or end_page > doc.page_count or start_page > end_page:
        raise ValueError(
            f"invalid range [{start_page}, {end_page}] for {doc.page_count}p doc"
        )
    parts = []
    for p in range(start_page, end_page + 1):
        parts.append(extract_page_text(doc, p))
    return "\n\n".join(filter(None, parts))
