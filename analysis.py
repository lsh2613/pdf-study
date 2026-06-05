"""scan_pdf / set_chapters 통합 로직.

이 모듈은 pdf/* + lang + workspace를 묶어 메인 LLM이 호출하기 쉬운
형태로 다듬는다. MCP 도구는 server.py에서 thin wrapper로 노출.
"""
from __future__ import annotations

import logging
from typing import Any

from . import lang, workspace
from .pdf import chapter as chapter_mod
from .pdf import reader
from .pdf import toc_finder

logger = logging.getLogger(__name__)

DEFAULT_SCAN_SIZE = 30  # 목차가 20p를 넘는 책이 있어 기본 스캔 범위를 넓힘
DEFAULT_CHUNK_SIZE = 30  # 목차 없을 때 균등 분할 단위

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
    page_count: int,
    ocr_mode: bool = False,
) -> dict[str, Any]:
    """비거부 recommendation에 offset 메타 + printed_range + 3택 안내 주입.

    physical_range(이 PDF의 물리 페이지 범위)와 printed_range_available(이 파일에
    실제 존재하는 책 페이지 범위 = [1, page_count - offset])을 함께 실어, LLM이
    '발췌본인데 목차엔 전체 책 챕터가 다 적힌' 경우 범위 밖 챕터를 제외하도록 한다.

    ocr_mode=True면 서버가 본문 텍스트를 신뢰하지 않으므로(스캔본),
    목차·offset을 scan_page_images(첫 N페이지 이미지)에서 직접 읽으라는
    OCR 안내를 next_step_guidance 앞에 덧붙인다.
    """
    _annotate_printed_ranges(reco["suggested_chapters"], page_offset)
    if reco.get("chunk_fallback"):
        _annotate_printed_ranges(reco["chunk_fallback"], page_offset)
    reco["page_offset"] = page_offset
    reco["offset_confidence"] = offset_confidence
    reco["user_choices"] = ["proceed", "manual_pdf_pages", "chunks"]
    off_known = page_offset is not None
    reco["physical_range"] = [1, page_count]
    # 이 파일에 실제 존재하는 책(인쇄) 페이지 범위. offset 미측정이면 null.
    reco["printed_range_available"] = (
        [1, page_count - page_offset] if off_known else None
    )

    # 발췌본(부분 PDF) 경고 — 두 모드 공통. 목차에 더 많은 챕터가 적혀 있어도
    # 이 파일의 물리 페이지를 벗어나는 챕터는 제외해야 한다.
    excerpt_note = (
        "⚠️ 이 PDF는 더 큰 책의 **일부(발췌본)**일 수 있습니다"
        + (f" — 실제 담긴 책 페이지는 약 p.1–{page_count - page_offset}뿐입니다. "
           "목차에 그 뒤 챕터가 더 적혀 있어도, 시작 책페이지가 이 범위를 넘으면 "
           "이 파일엔 없는 것이니 **그 챕터는 제외**하세요. 포함되는 마지막 챕터의 "
           f"끝 page_range는 PDF 마지막 페이지({page_count})로 둡니다. "
           if off_known else
           ". 목차의 챕터 중 이 PDF의 물리 페이지를 벗어나는 것은 제외하세요. ")
    )

    ocr_prefix = (
        "[OCR 모드] 이 PDF는 텍스트를 신뢰하지 않고 페이지 이미지를 직접 읽습니다. "
        "응답의 scan_page_images(첫 N페이지 JPEG 경로)를 먼저 읽어 ① 목차가 있으면 "
        "**최상위 챕터 항목(예: '01. 소개', '02. 설치와 설정')만** 골라 각 항목의 "
        "책 페이지번호를 읽으세요(하위 절 '1.1', '2.1.1'은 챕터가 아니니 무시). "
        "제목·페이지번호가 다른 줄에 있을 수 있습니다. 책 페이지 → 물리 = 책 + offset "
        "으로 변환해 from_toc 챕터를 구성하세요. suggested_chapters는 비어 있습니다 "
        "— 서버는 챕터를 제안하지 않으니 직접 채우세요. chunk_fallback(균등 청크)은 "
        "목차를 도저히 못 읽을 때만 쓰는 최후 수단입니다(그대로 추천하지 마세요). "
        "② 꼬리말 인쇄번호와 물리 페이지를 비교해 "
        "offset(물리 = 책 + offset)을 검증·추정하세요"
        + (f" (서버 텍스트 레이어 추정값 {page_offset}을 참고하되 이미지로 검증). "
           if off_known else " (서버가 offset을 못 구했으니 이미지로 직접 추정). ")
        + "또한 본문 언어를 파악해 set_chapters(language=\"ko\"|\"en\")로 전달하세요. "
        + excerpt_note
    ) if ocr_mode else excerpt_note

    reco["next_step_guidance"] = ocr_prefix + (
        "분석된 챕터를 사용자에게 보여줄 때 각 챕터를 "
        "'PDF p.{start}-{end} (책 p.{printed})' 형식으로 **두 번호 모두** 표기하세요"
        + ("." if off_known else " (offset 미측정 → 책 페이지는 '미상'으로 표기).")
        + " printed_range가 null이면 그 구간은 책 본문 번호가 없는 "
        "front matter(표지·서문)입니다. 그런 다음 반드시 세 갈래 선택을 받으세요: "
        "① 이대로 진행 → 위에서 정한 챕터(text 모드는 suggested_chapters, OCR 모드는 "
        "이미지로 분석한 챕터)를 그대로 set_chapters. "
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
    ocr_mode: bool = False,
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
    # OCR 모드는 텍스트 레이어를 신뢰하지 않으므로 텍스트 품질 기반 거부를
    # 모두 우회한다(스캔본·깨진 PDF가 바로 OCR의 대상). 목차도 (깨질 수 있는)
    # 텍스트 후보 대신 LLM이 scan_page_images로 직접 구성한다.
    if not ocr_mode and text_quality == "no_text_layer":
        return {
            "rejected": True,
            "reason": (
                "텍스트 레이어가 없는 PDF로 보입니다 (페이지당 평균 50자 미만). "
                "ocrmypdf 등으로 OCR 처리 후 다시 시도하거나, extraction_mode=\"ocr\" "
                "로 다시 init_work 하면 페이지 이미지를 직접 읽어 처리합니다."
            ),
            "primary_mode": None,
            "primary_reason": None,
            "suggested_chapters": [],
            "alternatives": [],
        }

    if not ocr_mode and text_quality == "garbled" and not allow_garbled:
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

    if ocr_mode:
        # OCR 모드: 서버는 챕터를 제안하지 않는다. 메인 에이전트가
        # scan_page_images(페이지 이미지)에서 목차를 직접 읽어 from_toc로
        # 구성해야 한다. 청크는 '목차를 못 읽을 때만' 쓰는 최후 수단이라
        # suggested_chapters가 아니라 chunk_fallback에 분리해 둔다(에이전트가
        # 이걸 '추천 챕터'로 오해해 그대로 제시하는 것을 막는다).
        return _with_offset_meta({
            "rejected": False,
            "reason": None,
            "primary_mode": "analyze_toc_from_images",
            "primary_reason": (
                "OCR 모드: 서버는 챕터를 제안하지 않습니다. scan_page_images에서 "
                "목차를 직접 읽어 챕터(from_toc)를 구성하세요. 목차를 도저히 못 "
                "읽을 때만 chunk_fallback(또는 single_unit)을 쓰세요."
            ),
            "suggested_chapters": [],  # 에이전트가 이미지 분석으로 채운다
            "chunk_fallback": chapter_mod.make_chunks(page_count, DEFAULT_CHUNK_SIZE),
            "alternatives": [
                f"chunk_fallback ({DEFAULT_CHUNK_SIZE}p 단위 균등 분할)",
                "single_unit (전체 1챕터)",
            ],
        }, page_offset, offset_confidence, page_count, ocr_mode)

    if not ocr_mode and toc_result.get("is_candidate"):
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
        }, page_offset, offset_confidence, page_count, ocr_mode)

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
        }, page_offset, offset_confidence, page_count, ocr_mode)

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
        }, page_offset, offset_confidence, page_count, ocr_mode)

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
    }, page_offset, offset_confidence, page_count, ocr_mode)


def _toc_entries_to_chapters(
    entries: list[dict[str, Any]],
    page_count: int,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    """목차 entries(title, 인쇄 page) → set_chapters용 chapters(물리 page_range).

    offset이 주어지면 물리 = 인쇄 + offset 로 보정한다. None이면 인쇄번호를
    물리로 간주(레거시 폴백) — 이 경우 next_step_guidance가 LLM에 본문 대조
    검증을 지시한다.

    **발췌본 처리**: 시작 물리 페이지가 page_count를 넘는 항목은 이 파일에 없는
    챕터(목차엔 전체 책 챕터가 다 적힘)이므로 page_count로 뭉개지 않고 **드롭**한다.
    그렇게 살아남은 마지막 챕터의 끝은 page_count로 둔다.
    """
    off = offset or 0
    # 물리 시작이 파일 범위 안(≤ page_count)인 항목만 남긴다.
    in_range = [e for e in entries if int(e["page"]) + off <= page_count]
    chapters: list[dict[str, Any]] = []
    for i, e in enumerate(in_range):
        start = max(1, int(e["page"]) + off)
        # 다음 entry의 시작 - 1 까지. 마지막은 page_count.
        if i + 1 < len(in_range):
            next_start = max(1, int(in_range[i + 1]["page"]) + off)
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
    ocr_mode = state.get("extraction_mode", "text") == "ocr"

    workspace.update_phase(work_id, "scanning", "in_progress")

    # PDF를 한 번만 열어 메타·페이지수·품질·텍스트를 모두 읽는다.
    # OCR 모드에서도 텍스트 추출은 best-effort로 수행한다 — offset(꼬리말 숫자)·
    # 언어(한글)는 글꼴 합자 깨짐에도 살아남아 공짜로 쓸 수 있기 때문. 다만
    # 본문/목차는 신뢰하지 않고, scan_page_images를 렌더해 LLM이 직접 읽는다.
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
        if ocr_mode:
            # OCR 모드: 목차 분석은 메인 에이전트가 scan_page_images(페이지 이미지)로
            # 직접 수행한다. 스캔본·깨진 텍스트를 스크립트로 파싱하면 쓰레기 항목만
            # 나와 에이전트를 오히려 헷갈리게 하므로 toc_finder를 돌리지 않는다.
            toc_result = {
                "has_toc_keyword": False,
                "entries": [],
                "is_candidate": False,
                "note": "ocr 모드: 목차는 메인 에이전트가 scan_page_images로 직접 분석",
            }
        else:
            toc_result = toc_finder.find_toc_candidates(scanned_text)

        # 인쇄 페이지번호 ↔ PDF 물리 인덱스 오프셋 측정 (꼬리말 번호 다수결)
        offset_info = reader.detect_page_offset(doc)

        # OCR 모드: 첫 N페이지를 JPEG로 렌더해 LLM이 목차/offset/언어를 읽게 한다
        scan_page_images: list[dict[str, Any]] = []
        if ocr_mode and scan_end > 0:
            scan_page_images = reader.render_pages(
                doc, 1, scan_end, workspace.pages_dir(work_id),
            )
    finally:
        doc.close()

    page_offset = offset_info["offset"]
    offset_confidence = offset_info["confidence"]

    recommendations = _build_recommendations(
        page_count, toc_result, text_quality,
        text_sample=scanned_text, allow_garbled=allow_garbled,
        page_offset=page_offset, offset_confidence=offset_confidence,
        ocr_mode=ocr_mode,
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
        "extraction_mode": "ocr" if ocr_mode else "text",
        "text_quality": text_quality,
        "language": language,
        "page_offset": page_offset,
        "page_offset_confidence": offset_confidence,
        "toc_candidates": toc_result,
        "recommendations": recommendations,
    })

    return {
        "page_count": page_count,
        "extraction_mode": "ocr" if ocr_mode else "text",
        "book_metadata": book_metadata,
        "text_quality": text_quality,
        "avg_chars_per_page": quality["avg_chars_per_page"],
        "language": language,
        "page_offset": page_offset,
        "page_offset_confidence": offset_confidence,
        # OCR 모드에서만 채워진다 — LLM이 직접 읽을 첫 N페이지 이미지 경로
        "scan_page_images": scan_page_images,
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
    language: str = "",
) -> dict[str, Any]:
    """챕터 구조 확정 → 챕터별 텍스트 추출 + 저장.

    Args:
        chapters: [{"chapter_id", "title", "page_range"=[start,end]}, ...]
                  (1-based inclusive)
        book_info: 메인 LLM이 보강한 책 정보. None이면 PDF 메타만 사용.
        language: "ko" | "en". OCR 모드에서 LLM이 이미지로 파악한 본문 언어를
                  전달하면 state.language를 갱신한다(텍스트 감지가 불가능하므로).

    Returns:
        {"chapter_count", "total_chars", "chapters": [...]}

    OCR 모드(state.extraction_mode == "ocr")에서는 본문 텍스트를 추출하지 않는다.
    서브에이전트가 get_chapter_content가 렌더한 페이지 이미지를 직접 읽기 때문.
    (그림 추출은 더 이상 하지 않는다 — 요약은 텍스트/마크다운만 다룬다.)
    """
    if not chapters:
        raise ValueError("chapters must not be empty")

    state = workspace.load_state(work_id)
    pdf_path = state["pdf_path"]
    ocr_mode = state.get("extraction_mode", "text") == "ocr"
    page_count = state.get("page_count")
    if page_count is None:
        raise RuntimeError(
            "page_count not in state. call scan_pdf before set_chapters."
        )

    # OCR 모드: LLM이 파악한 언어를 state에 반영 (텍스트 감지가 불가능)
    if language and language.lower() in ("ko", "en"):
        workspace.update_state(work_id, language=language.lower())

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

    # 챕터별 추출
    workspace.update_phase(work_id, "chapter_processing", "in_progress")

    summaries: list[dict[str, Any]] = []
    total_chars = 0

    doc = reader.open_pdf(pdf_path)
    try:
        for ch_def in normalized:
            cid = ch_def["chapter_id"]

            # 비본문 챕터(찾아보기·색인·판권 등)는 추출도, sub-agent 디스패치도, 렌더도 안 한다
            if ch_def.get("skip"):
                summaries.append({
                    "chapter_id": cid, "title": ch_def["title"],
                    "page_range": ch_def["page_range"],
                    "char_count": 0,
                    "skipped": True, "error": None,
                })
                continue

            try:
                if ocr_mode:
                    # 본문 텍스트는 추출하지 않는다(서브에이전트가 페이지 이미지를
                    # 직접 읽음).
                    char_count = 0
                else:
                    extracted = chapter_mod.extract_chapter(doc, ch_def)
                    char_count = extracted["char_count"]
            except Exception as e:
                logger.exception("chapter extraction failed: %s", cid)
                workspace.mark_chapter_failed(work_id, cid, kind="summary", error=str(e))
                summaries.append({
                    "chapter_id": cid, "title": ch_def["title"],
                    "page_range": ch_def["page_range"],
                    "char_count": 0, "error": str(e),
                })
                continue

            raw_payload = {
                "chapter_id": cid,
                "title": ch_def["title"],
                "page_range": ch_def["page_range"],
                "char_count": char_count,
            }
            if not ocr_mode:
                raw_payload["text"] = extracted["text"]
            workspace.save_chapter_raw(work_id, cid, raw_payload)
            workspace.update_chapter_status(work_id, cid, char_count=char_count)

            total_chars += char_count
            summaries.append({
                "chapter_id": cid,
                "title": ch_def["title"],
                "page_range": ch_def["page_range"],
                "char_count": char_count,
                "error": None,
            })
    finally:
        doc.close()

    return {
        "chapter_count": len(normalized),
        "total_chars": total_chars,
        "chapters": summaries,
    }


# ---------------------------------------------------------------------------
# get_chapter_content
# ---------------------------------------------------------------------------

def get_chapter_content_impl(work_id: str, chapter_id: str) -> dict[str, Any]:
    """챕터 raw 데이터 반환. OCR 모드면 페이지를 lazy 렌더해 page_images 첨부.

    - text 모드: chapters_raw의 {text}를 그대로 반환.
    - ocr 모드: 본문 텍스트가 없으므로 page_range의 페이지를 JPEG로 렌더해
      page_images(서브에이전트가 직접 읽을 페이지 이미지 절대경로)를 채운다.
      이미 렌더된 페이지는 재사용한다(p{N}.jpg 캐시).

    chapter_id는 **set_chapters로 등록된 id(ch1·ch2·…)**여야 한다. 페이지 범위
    같은 임의 문자열('p11-p18' 등)을 주면 등록 챕터 목록을 담아 거부한다 —
    특정 페이지를 그냥 보려면 scan_pdf의 scan_page_images 경로를 직접 열면 된다.
    """
    state = workspace.load_state(work_id)
    chapters = state.get("chapters", {})

    if chapter_id not in chapters:
        valid = [cid for cid, c in chapters.items() if not c.get("skip")]
        hint = (
            f"등록된 본문 chapter_id 중에서 고르세요: {valid}. "
            if valid else
            "아직 set_chapters로 등록된 챕터가 없습니다 — 먼저 set_chapters를 호출하세요. "
        )
        raise FileNotFoundError(
            f"unknown chapter_id: {chapter_id!r}. {hint}"
            "(get_chapter_content는 set_chapters로 등록된 챕터 전용입니다. "
            "목차 분석 등으로 특정 페이지를 보려면 scan_pdf 응답의 "
            "scan_page_images[].path 이미지를 직접 열어 읽으세요 — 이 도구로 "
            "페이지 범위를 가져오는 게 아닙니다.)"
        )

    if chapters[chapter_id].get("skip"):
        raise FileNotFoundError(
            f"chapter_id {chapter_id!r}는 skip(비본문: 표지·목차·색인 등)으로 "
            "표시돼 추출 대상이 아닙니다. 본문 챕터만 처리하세요."
        )

    raw = workspace.get_chapter_raw(work_id, chapter_id)  # 없으면 FileNotFoundError

    if state.get("extraction_mode", "text") == "ocr":
        start, end = raw["page_range"]
        doc = reader.open_pdf(state["pdf_path"])
        try:
            raw["page_images"] = reader.render_pages(
                doc, int(start), int(end), workspace.pages_dir(work_id),
            )
        finally:
            doc.close()
    return raw
