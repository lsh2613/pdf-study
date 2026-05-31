"""scan_pdf / set_chapters 통합 로직.

이 모듈은 pdf/* + lang + workspace를 묶어 메인 LLM이 호출하기 쉬운
형태로 다듬는다. MCP 도구는 server.py에서 thin wrapper로 노출.
"""
from __future__ import annotations

import logging
from typing import Any

from . import lang, workspace
from .pdf import chapter as chapter_mod
from .pdf import images as images_mod
from .pdf import reader
from .pdf import toc_finder

logger = logging.getLogger(__name__)

DEFAULT_SCAN_SIZE = 20
DEFAULT_CHUNK_SIZE = 20

# 페이지 임계값 (docs/03-pdf-processing.md)
SHORT_PDF_THRESHOLD = 50         # < 50p → single_unit 권장
LARGE_PDF_THRESHOLD = 200        # 200p+ & 목차 없음 → chunks 권장 (single은 LLM 부담)


# ---------------------------------------------------------------------------
# scan_pdf
# ---------------------------------------------------------------------------

def _build_recommendations(
    page_count: int,
    toc_result: dict[str, Any],
    text_quality: str,
) -> dict[str, Any]:
    """페이지 수 + 목차 후보 + 품질로 챕터 분리 모드 추천.

    Returns:
        {
            "rejected": bool,
            "reason": str,
            "primary_mode": "from_toc" | "single_unit" | "chunks" | "ask_user",
            "primary_reason": str,
            "suggested_chapters": [...],   # primary_mode로 즉시 set_chapters 가능
            "alternatives": [str, ...],
        }
    """
    if text_quality == "no_text_layer":
        return {
            "rejected": True,
            "reason": (
                "텍스트 레이어가 없는 PDF로 보입니다 (페이지당 평균 50자 미만). "
                "ocrmypdf 등으로 OCR 처리 후 다시 시도해주세요."
            ),
            "primary_mode": None,
            "primary_reason": None,
            "suggested_chapters": [],
            "alternatives": [],
        }

    if toc_result.get("is_candidate"):
        # 목차 후보를 그대로 챕터로 변환 — 마지막 항목의 끝 페이지는 page_count로 종료
        entries = toc_result["entries"]
        suggested = _toc_entries_to_chapters(entries, page_count)
        return {
            "rejected": False,
            "reason": None,
            "primary_mode": "from_toc",
            "primary_reason": f"본문에서 목차 후보 {len(entries)}개를 찾았습니다.",
            "suggested_chapters": suggested,
            "alternatives": [
                f"chunks ({DEFAULT_CHUNK_SIZE}p 단위 균등 분할)",
                "single_unit (전체 1챕터)",
            ],
        }

    if page_count < SHORT_PDF_THRESHOLD:
        suggested = [{
            "chapter_id": "ch1",
            "title": "전체",
            "page_range": [1, page_count],
        }]
        return {
            "rejected": False,
            "reason": None,
            "primary_mode": "single_unit",
            "primary_reason": f"짧은 PDF({page_count}p)이므로 전체를 1챕터로 처리합니다.",
            "suggested_chapters": suggested,
            "alternatives": [f"chunks ({DEFAULT_CHUNK_SIZE}p 단위)"],
        }

    if page_count >= LARGE_PDF_THRESHOLD:
        suggested = chapter_mod.make_chunks(page_count, DEFAULT_CHUNK_SIZE)
        return {
            "rejected": False,
            "reason": None,
            "primary_mode": "chunks",
            "primary_reason": (
                f"{page_count}p 분량 & 목차 미감지. "
                f"{DEFAULT_CHUNK_SIZE}p 단위 균등 분할을 권장합니다. "
                "single_unit으로 처리하면 sub-agent 부담이 매우 큽니다."
            ),
            "suggested_chapters": suggested,
            "alternatives": ["from_toc (사용자가 목차 직접 입력)"],
        }

    # 50 ≤ page_count < 200 & 목차 없음 → 사용자 의사 확인 권장
    suggested = chapter_mod.make_chunks(page_count, DEFAULT_CHUNK_SIZE)
    return {
        "rejected": False,
        "reason": None,
        "primary_mode": "ask_user",
        "primary_reason": (
            f"{page_count}p 분량 & 목차 미감지. "
            "전체 1챕터(single_unit)와 균등 분할(chunks) 중 사용자에게 확인해주세요."
        ),
        "suggested_chapters": suggested,  # chunks fallback을 미리 제공
        "alternatives": [
            "single_unit (전체 1챕터)",
            f"chunks ({DEFAULT_CHUNK_SIZE}p 단위)",
        ],
    }


def _toc_entries_to_chapters(
    entries: list[dict[str, Any]],
    page_count: int,
) -> list[dict[str, Any]]:
    """목차 entries(title, page) → set_chapters용 chapters(page_range)."""
    chapters: list[dict[str, Any]] = []
    for i, e in enumerate(entries):
        start = max(1, min(int(e["page"]), page_count))
        # 다음 entry의 시작 - 1 까지. 마지막은 page_count.
        if i + 1 < len(entries):
            next_start = max(1, min(int(entries[i + 1]["page"]), page_count))
            end = max(start, next_start - 1)
        else:
            end = page_count
        chapters.append({
            "chapter_id": f"ch{i + 1}",
            "title": e["title"],
            "page_range": [start, end],
        })
    return chapters


def scan_pdf_impl(work_id: str, scan_size: int = DEFAULT_SCAN_SIZE) -> dict[str, Any]:
    """PDF 스캔: 메타/품질/언어/목차 후보/챕터 추천을 모두 모아 반환.

    state.json에 language, page_count, text_quality를 채우고
    phases.scanning을 completed로 갱신한다.
    """
    state = workspace.load_state(work_id)
    pdf_path = state["pdf_path"]

    workspace.update_phase(work_id, "scanning", "in_progress")

    info = reader.get_pdf_info(pdf_path)
    page_count = info["page_count"]
    book_metadata = info["book_metadata"]

    doc = reader.open_pdf(pdf_path)
    try:
        quality = reader.evaluate_text_quality(doc)
        text_quality = quality["quality"]

        # 첫 N페이지 텍스트 → 언어 감지 + 목차 후보용
        scan_end = min(scan_size, page_count) if page_count else 0
        if scan_end > 0:
            scanned_text = reader.extract_text_range(doc, 1, scan_end)
        else:
            scanned_text = ""

        language = lang.detect_language(scanned_text)
        toc_result = toc_finder.find_toc_candidates(scanned_text)
    finally:
        doc.close()

    recommendations = _build_recommendations(page_count, toc_result, text_quality)

    # state 갱신
    workspace.update_state(
        work_id,
        page_count=page_count,
        text_quality=text_quality,
        language=language,
    )
    workspace.update_phase(work_id, "scanning", "completed")

    # outline.json 저장 (목차 후보 + 추천)
    workspace.save_outline(work_id, {
        "page_count": page_count,
        "text_quality": text_quality,
        "language": language,
        "toc_candidates": toc_result,
        "recommendations": recommendations,
    })

    return {
        "page_count": page_count,
        "book_metadata": book_metadata,
        "text_quality": text_quality,
        "avg_chars_per_page": quality["avg_chars_per_page"],
        "language": language,
        "scanned_text": scanned_text,
        "toc_candidates": toc_result,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# set_chapters
# ---------------------------------------------------------------------------

def _validate_chapter_def(ch: dict[str, Any], page_count: int) -> dict[str, Any]:
    if "chapter_id" not in ch or "title" not in ch or "page_range" not in ch:
        raise ValueError(
            f"chapter must have chapter_id, title, page_range; got {ch!r}"
        )
    pr = ch["page_range"]
    if not (isinstance(pr, (list, tuple)) and len(pr) == 2):
        raise ValueError(f"chapter {ch['chapter_id']}: page_range must be [start, end]")
    start, end = int(pr[0]), int(pr[1])
    if start < 1 or end > page_count or start > end:
        raise ValueError(
            f"chapter {ch['chapter_id']}: page_range [{start}, {end}] "
            f"invalid for {page_count}p document"
        )
    out = {"chapter_id": str(ch["chapter_id"]), "title": str(ch["title"]),
           "page_range": [start, end]}
    if ch.get("skip"):
        out["skip"] = True
    return out


def set_chapters_impl(
    work_id: str,
    chapters: list[dict[str, Any]],
    book_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """챕터 구조 확정 → 챕터별 텍스트/이미지 추출 + 저장.

    Args:
        chapters: [{"chapter_id", "title", "page_range"=[start,end]}, ...]
                  (1-based inclusive)
        book_info: 메인 LLM이 보강한 책 정보. None이면 PDF 메타만 사용.

    Returns:
        {"chapter_count", "total_chars", "total_images", "chapters": [...]}
    """
    if not chapters:
        raise ValueError("chapters must not be empty")

    state = workspace.load_state(work_id)
    pdf_path = state["pdf_path"]
    page_count = state.get("page_count")
    if page_count is None:
        raise RuntimeError(
            "page_count not in state. call scan_pdf before set_chapters."
        )

    # 검증 + 정규화
    normalized = [_validate_chapter_def(ch, page_count) for ch in chapters]
    ids = [ch["chapter_id"] for ch in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate chapter_ids: {ids}")

    # state.chapters 채우기 (status=pending)
    workspace.set_chapters_in_state(work_id, normalized)

    # book_info 저장: 없으면 PDF 메타로 fallback
    if book_info is None:
        info = reader.get_pdf_info(pdf_path)
        meta = info["book_metadata"]
        book_info = {
            "title": meta.get("title") or "Untitled",
            "author": meta.get("author") or "",
            "subject": meta.get("subject") or "",
        }
    workspace.save_book_info(work_id, book_info)

    images_out_dir = workspace.images_dir(work_id)

    # 챕터별 추출
    workspace.update_phase(work_id, "chapter_processing", "in_progress")

    summaries: list[dict[str, Any]] = []
    total_chars = 0
    total_images = 0

    doc = reader.open_pdf(pdf_path)
    try:
        for ch_def in normalized:
            cid = ch_def["chapter_id"]

            # 비본문 챕터(찾아보기·색인·판권 등)는 추출도, sub-agent 디스패치도, 렌더도 안 한다
            if ch_def.get("skip"):
                summaries.append({
                    "chapter_id": cid, "title": ch_def["title"],
                    "page_range": ch_def["page_range"],
                    "char_count": 0, "image_count": 0,
                    "skipped": True, "error": None,
                })
                continue

            try:
                extracted = chapter_mod.extract_chapter(doc, ch_def)
                image_refs = images_mod.extract_chapter_images(
                    doc, cid, ch_def["page_range"], images_out_dir,
                )
            except Exception as e:
                logger.exception("chapter extraction failed: %s", cid)
                workspace.mark_chapter_failed(work_id, cid, kind="summary", error=str(e))
                summaries.append({
                    "chapter_id": cid, "title": ch_def["title"],
                    "page_range": ch_def["page_range"],
                    "char_count": 0, "image_count": 0, "error": str(e),
                })
                continue

            raw_payload = {
                "chapter_id": cid,
                "title": ch_def["title"],
                "page_range": ch_def["page_range"],
                "text": extracted["text"],
                "char_count": extracted["char_count"],
                "image_refs": image_refs,  # 절대 경로 포함
            }
            workspace.save_chapter_raw(work_id, cid, raw_payload)
            workspace.update_chapter_status(work_id, cid,
                char_count=extracted["char_count"])

            total_chars += extracted["char_count"]
            total_images += len(image_refs)
            summaries.append({
                "chapter_id": cid,
                "title": ch_def["title"],
                "page_range": ch_def["page_range"],
                "char_count": extracted["char_count"],
                "image_count": len(image_refs),
                "error": None,
            })
    finally:
        doc.close()

    return {
        "chapter_count": len(normalized),
        "total_chars": total_chars,
        "total_images": total_images,
        "chapters": summaries,
    }
