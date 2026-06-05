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

DEFAULT_SCAN_SIZE = 30  # 목차가 20p를 넘는 책이 있어 기본 스캔 범위를 넓힘
DEFAULT_CHUNK_SIZE = 20

# 페이지 임계값 (docs/03-pdf-processing.md)
SHORT_PDF_THRESHOLD = 50         # < 50p → single_unit 권장
LARGE_PDF_THRESHOLD = 200        # 200p+ & 목차 없음 → chunks 권장 (single은 LLM 부담)


# ---------------------------------------------------------------------------
# scan_pdf
# ---------------------------------------------------------------------------

GARBLED_SAMPLE_CHARS = 600  # 사용자에게 보여줄 깨진 텍스트 샘플 길이


def _annotate_printed_ranges(
    chapters: list[dict[str, Any]],
    offset: int | None,
) -> list[dict[str, Any]]:
    """각 챕터(물리 page_range)에 인쇄(책) 페이지 printed_range를 덧붙인다.

    책 페이지 = 물리 − offset. offset 미측정이면 None.
    전 구간이 front matter(책 번호 < 1)면 None, 일부만이면 1로 클램프.
    """
    for ch in chapters:
        if offset is None:
            ch["printed_range"] = None
            continue
        s, e = ch["page_range"]
        ps, pe = s - offset, e - offset
        ch["printed_range"] = None if pe < 1 else [max(1, ps), pe]
    return chapters


def _with_offset_meta(
    reco: dict[str, Any],
    page_offset: int | None,
    offset_confidence: str,
) -> dict[str, Any]:
    """비거부 recommendation에 offset 메타 + printed_range + 3택 안내 주입."""
    _annotate_printed_ranges(reco["suggested_chapters"], page_offset)
    reco["page_offset"] = page_offset
    reco["offset_confidence"] = offset_confidence
    reco["user_choices"] = ["proceed", "manual_pdf_pages", "chunks"]
    off_known = page_offset is not None
    reco["next_step_guidance"] = (
        "분석된 챕터를 사용자에게 보여줄 때 각 챕터를 "
        "'PDF p.{start}-{end} (책 p.{printed})' 형식으로 **두 번호 모두** 표기하세요"
        + ("." if off_known else " (offset 미측정 → 책 페이지는 '미상'으로 표기).")
        + " printed_range가 null이면 그 구간은 책 본문 번호가 없는 "
        "front matter(표지·서문)입니다. 그런 다음 반드시 세 갈래 선택을 받으세요: "
        "① 이대로 진행 → suggested_chapters를 그대로 set_chapters. "
        "② 직접 입력 → 사용자에게 **PDF(물리) 페이지 번호로** 챕터 범위를 받으세요 "
        "(set_chapters는 PDF 물리 페이지 기준). "
        + (f"사용자가 책 페이지로 말하면 물리 = 책 + {page_offset} 로 변환해 주세요. "
           if off_known else "offset 미측정이라 변환 불가하니 PDF 페이지로 직접 받으세요. ")
        + "③ 청크 → N페이지 균등 분할. "
        "offset_confidence가 'high'가 아니거나 from_toc 경계가 의심되면, 첫 1~2개 "
        "챕터 제목이 계산된 PDF 페이지(±1)에 실제로 나오는지 본문을 직접 읽어 "
        "확인하고, 어긋나면 page_range를 보정한 뒤 사용자에게 제시하세요."
    )
    return reco


def _build_recommendations(
    page_count: int,
    toc_result: dict[str, Any],
    text_quality: str,
    text_sample: str = "",
    allow_garbled: bool = False,
    page_offset: int | None = None,
    offset_confidence: str = "none",
) -> dict[str, Any]:
    """페이지 수 + 목차 후보 + 품질 + 페이지 오프셋으로 챕터 분리 모드 추천.

    allow_garbled=True 면 모지바케 거부를 건너뛰고 깨진 텍스트 그대로
    페이지 수 기반 라우팅을 진행한다 (사용자가 샘플 확인 후 강행 선택한 경우).

    page_offset: 물리 = 인쇄(책) + offset. None이면 미측정.
    비거부 응답에는 _with_offset_meta로 page_offset/offset_confidence,
    각 챕터의 printed_range(책 페이지), user_choices, next_step_guidance가 붙는다.

    Returns:
        {
            "rejected": bool,
            "reason": str,
            "primary_mode": "from_toc" | "single_unit" | "chunks" | "ask_user",
            "primary_reason": str,
            "suggested_chapters": [{... "page_range":[s,e], "printed_range":[s,e]|None}],
            "alternatives": [str, ...],
            "page_offset": int | None,
            "offset_confidence": "high" | "low" | "none",
            "user_choices": ["proceed", "manual_pdf_pages", "chunks"],
            "next_step_guidance": str,
            "text_sample": str,            # garbled 거부 시 사용자 확인용 샘플
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

    if text_quality == "garbled" and not allow_garbled:
        sample = text_sample.strip()[:GARBLED_SAMPLE_CHARS]
        return {
            "rejected": True,
            "reason": (
                "텍스트는 추출되지만 인코딩이 깨져 있습니다(모지바케). "
                "글꼴의 ToUnicode 매핑이 손상된 PDF입니다. "
                "아래 text_sample을 사용자에게 그대로 보여주고 세 갈래 중 "
                "선택을 받아 주세요.\n"
                "① 원본에서 일부 페이지만 따로 추출한 파일이라면 — "
                "Preview·'PDF로 인쇄' 같은 경로가 글꼴을 재인코딩하며 깨뜨린 "
                "경우입니다. 원본에서 객체를 무손실 복사하는 도구로 다시 "
                "추출해 주세요. 예: "
                "`qpdf 원본.pdf --pages 원본.pdf 1-20 -- out.pdf` "
                "(또는 `pdftk`, `mutool merge`).\n"
                "② 원본 자체가 이렇게 깨진다면 — 정상 텍스트 레이어가 없는 "
                "것이므로 `ocrmypdf --force-ocr -l kor+eng in.pdf out.pdf` 로 "
                "OCR 처리 후 다시 시도해 주세요.\n"
                "③ 샘플을 확인했는데 이대로 진행해도 괜찮다고 사용자가 "
                "판단하면 — `scan_pdf(work_id, allow_garbled=True)` 로 다시 "
                "호출하면 깨진 텍스트 그대로 요약을 진행합니다 (품질은 보장되지 "
                "않음)."
            ),
            "primary_mode": None,
            "primary_reason": None,
            "suggested_chapters": [],
            "alternatives": [],
            "text_sample": sample,
        }

    if toc_result.get("is_candidate"):
        # 목차 후보를 챕터로 변환 (인쇄→물리 offset 보정). 마지막 끝은 page_count.
        entries = toc_result["entries"]
        suggested = _toc_entries_to_chapters(entries, page_count, page_offset)
        return _with_offset_meta({
            "rejected": False,
            "reason": None,
            "primary_mode": "from_toc",
            "primary_reason": f"본문에서 목차 후보 {len(entries)}개를 찾았습니다.",
            "suggested_chapters": suggested,
            "alternatives": [
                f"chunks ({DEFAULT_CHUNK_SIZE}p 단위 균등 분할)",
                "single_unit (전체 1챕터)",
            ],
        }, page_offset, offset_confidence)

    if page_count < SHORT_PDF_THRESHOLD:
        suggested = [{
            "chapter_id": "ch1",
            "title": "전체",
            "page_range": [1, page_count],
        }]
        return _with_offset_meta({
            "rejected": False,
            "reason": None,
            "primary_mode": "single_unit",
            "primary_reason": f"짧은 PDF({page_count}p)이므로 전체를 1챕터로 처리합니다.",
            "suggested_chapters": suggested,
            "alternatives": [f"chunks ({DEFAULT_CHUNK_SIZE}p 단위)"],
        }, page_offset, offset_confidence)

    if page_count >= LARGE_PDF_THRESHOLD:
        suggested = chapter_mod.make_chunks(page_count, DEFAULT_CHUNK_SIZE)
        return _with_offset_meta({
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
        }, page_offset, offset_confidence)

    # 50 ≤ page_count < 200 & 목차 없음 → 사용자 의사 확인 권장
    suggested = chapter_mod.make_chunks(page_count, DEFAULT_CHUNK_SIZE)
    return _with_offset_meta({
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
    }, page_offset, offset_confidence)


def _toc_entries_to_chapters(
    entries: list[dict[str, Any]],
    page_count: int,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    """목차 entries(title, 인쇄 page) → set_chapters용 chapters(물리 page_range).

    offset이 주어지면 물리 = 인쇄 + offset 로 보정한다. None이면 인쇄번호를
    물리로 간주(레거시 폴백) — 이 경우 next_step_guidance가 LLM에 본문 대조
    검증을 지시한다.
    """
    off = offset or 0
    chapters: list[dict[str, Any]] = []
    for i, e in enumerate(entries):
        start = max(1, min(int(e["page"]) + off, page_count))
        # 다음 entry의 시작 - 1 까지. 마지막은 page_count.
        if i + 1 < len(entries):
            next_start = max(1, min(int(entries[i + 1]["page"]) + off, page_count))
            end = max(start, next_start - 1)
        else:
            end = page_count
        chapters.append({
            "chapter_id": f"ch{i + 1}",
            "title": e["title"],
            "page_range": [start, end],
        })
    return chapters


def scan_pdf_impl(
    work_id: str,
    scan_size: int = DEFAULT_SCAN_SIZE,
    allow_garbled: bool = False,
) -> dict[str, Any]:
    """PDF 스캔: 메타/품질/언어/목차 후보/챕터 추천을 모두 모아 반환.

    state.json에 language, page_count, text_quality를 채우고
    phases.scanning을 completed로 갱신한다.
    """
    state = workspace.load_state(work_id)
    pdf_path = state["pdf_path"]

    workspace.update_phase(work_id, "scanning", "in_progress")

    # PDF를 한 번만 열어 메타·페이지수·품질·텍스트를 모두 읽는다.
    doc = reader.open_pdf(pdf_path)
    try:
        page_count = doc.page_count
        book_metadata = reader.extract_metadata(doc)

        quality = reader.evaluate_text_quality(doc)
        text_quality = quality["quality"]

        # 첫 N페이지 텍스트 → 언어 감지 + 목차 후보용
        scan_end = min(scan_size, page_count) if page_count else 0
        scanned_text = (
            reader.extract_text_range(doc, 1, scan_end) if scan_end > 0 else ""
        )

        language = lang.detect_language(scanned_text)
        toc_result = toc_finder.find_toc_candidates(scanned_text)

        # 인쇄 페이지번호 ↔ PDF 물리 인덱스 오프셋 측정 (꼬리말 번호 다수결)
        offset_info = reader.detect_page_offset(doc)
    finally:
        doc.close()

    page_offset = offset_info["offset"]
    offset_confidence = offset_info["confidence"]

    recommendations = _build_recommendations(
        page_count, toc_result, text_quality,
        text_sample=scanned_text, allow_garbled=allow_garbled,
        page_offset=page_offset, offset_confidence=offset_confidence,
    )

    # state 갱신
    workspace.update_state(
        work_id,
        page_count=page_count,
        text_quality=text_quality,
        language=language,
        page_offset=page_offset,
        page_offset_confidence=offset_confidence,
    )
    workspace.update_phase(work_id, "scanning", "completed")

    # outline.json 저장 (목차 후보 + 추천)
    workspace.save_outline(work_id, {
        "page_count": page_count,
        "text_quality": text_quality,
        "language": language,
        "page_offset": page_offset,
        "page_offset_confidence": offset_confidence,
        "toc_candidates": toc_result,
        "recommendations": recommendations,
    })

    return {
        "page_count": page_count,
        "book_metadata": book_metadata,
        "text_quality": text_quality,
        "avg_chars_per_page": quality["avg_chars_per_page"],
        "language": language,
        "page_offset": page_offset,
        "page_offset_confidence": offset_confidence,
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
    # printed_range(책 페이지)는 표시용 메타 — 있으면 보존, 검증은 하지 않음
    # (책 번호는 PDF 페이지와 체계가 달라 page_count로 검증할 수 없다).
    pr_printed = ch.get("printed_range")
    if isinstance(pr_printed, (list, tuple)) and len(pr_printed) == 2:
        out["printed_range"] = [int(pr_printed[0]), int(pr_printed[1])]
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
