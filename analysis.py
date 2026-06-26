"""scan_pdf / set_chapters 통합 로직.

이 모듈은 pdf/* + lang + workspace를 묶어 메인 LLM이 호출하기 쉬운
형태로 다듬는다. MCP 도구는 server.py에서 thin wrapper로 노출.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import lang, workspace
from .pdf import chapter as chapter_mod
from .pdf import ocr
from .pdf import reader

logger = logging.getLogger(__name__)

DEFAULT_SCAN_SIZE = 30  # 목차가 20p를 넘는 책이 있어 기본 스캔 범위를 넓힘
DEFAULT_CHUNK_SIZE = 30  # 목차 없을 때 균등 분할 단위

# 모든 "선택지를 사용자에게 제시" 지점에 공통으로 붙이는 정책. 메인 에이전트가
# MCP 선택지를 자기 말로 풀어쓰거나(요약/번역), '권장·기본값' 같은 표현을 임의로
# 덧붙이는 드리프트를 막기 위함. server.py의 거부 메시지도 이걸 재사용한다.
CHOICE_POLICY = (
    "[선택지 제시 규칙] 이 선택지는 MCP가 준 것이다. 클라이언트에 **구조화된 "
    "선택 도구가 있으면 반드시 그 도구로 물어라**(예: Claude Code의 AskUserQuestion) "
    "— data.choices(또는 user_choices)를 옵션으로 그대로 넣어라. 없으면 번호 목록으로 "
    "보여줘라. 어느 경우든 각 항목의 label·설명을 **그대로** 쓰고, 문구를 요약·변형하지 "
    "말며, 항목을 합치거나 빼거나 새로 만들지 말고, MCP가 명시하지 않은 '추천·기본값' "
    "표현을 임의로 덧붙이지 마라. 사용자가 고른 값만 전달해 재호출하라. "
)


# ---------------------------------------------------------------------------
# scan_pdf
# ---------------------------------------------------------------------------


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
    source: str = "vision",
) -> dict[str, Any]:
    """비거부 recommendation에 offset 메타 + printed_range + 선택지 안내 주입.

    physical_range(이 PDF의 물리 페이지 범위)와 printed_range_available(이 파일에
    실제 존재하는 책 페이지 범위 = [1, page_count - offset])을 함께 실어, LLM이
    '발췌본인데 목차엔 전체 책 챕터가 다 적힌' 경우 범위 밖 챕터를 제외하도록 한다.

    source:
      - "outline": 내장 목차(북마크)로 챕터를 구성함. 사용자에게 보여 확인받고,
        틀리면 force_vision=True로 목차 이미지 OCR 재스캔을 하라는 4택 안내.
      - "vision":  legacy 이름. 내장 목차가 없어 toc_page_images와 서버 OCR
        텍스트를 바탕으로 챕터를 구성해야 함. 텍스트/스크립트 추정 금지 + 3택.
    """
    _annotate_printed_ranges(reco["suggested_chapters"], page_offset)
    if reco.get("chunk_fallback"):
        _annotate_printed_ranges(reco["chunk_fallback"], page_offset)
    reco["page_offset"] = page_offset
    reco["offset_confidence"] = offset_confidence
    off_known = page_offset is not None
    reco["physical_range"] = [1, page_count]
    # 이 파일에 실제 존재하는 책(인쇄) 페이지 범위. offset 미측정이면 null.
    reco["printed_range_available"] = (
        [1, page_count - page_offset] if off_known else None
    )

    # 발췌본(부분 PDF) 경고 — 목차에 더 많은 챕터가 적혀 있어도
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

    two_number = (
        "각 챕터를 사용자에게 보여줄 때 'PDF p.{start}-{end} (책 p.{printed})' "
        "형식으로 **두 번호 모두** 표기하세요"
        + ("." if off_known else " (offset 미측정 → 책 페이지는 '미상'으로 표기).")
        + " printed_range가 null이면 책 번호가 없는 front matter(표지·서문)입니다. "
    )
    manual_chunk = (
        "직접 입력을 고르면 챕터 범위를 **PDF(물리) 페이지**로 받으세요"
        + (f" (사용자가 책 페이지로 말하면 물리 = 책 + {page_offset} 로 변환). "
           if off_known else " (offset 미측정이라 PDF 페이지로 직접 받으세요). ")
        + "청크를 고르면 N페이지 균등 분할. "
    )
    # 모든 단계 공통 정책 (CHOICE_POLICY): 구조화 선택 도구로 user_choices를
    # 그대로 제시하고, 임의로 합치거나 빼거나 권장 표현을 덧붙이지 말 것.
    choices_policy = "그런 다음 " + CHOICE_POLICY

    if source == "outline":
        reco["user_choices"] = [
            "proceed", "reanalyze_with_vision", "manual_pdf_pages", "chunks",
        ]
        reco["next_step_guidance"] = (
            "[내장 목차] PDF 북마크(get_toc)에서 챕터를 구성했습니다 — 물리 페이지를 "
            "직접 가리켜 offset/OCR 없이 정확합니다. " + excerpt_note + two_number
            + choices_policy
            + "① 이대로 진행 → suggested_chapters를 그대로 set_chapters. "
            "② 목차가 틀림 → scan_pdf(work_id, force_vision=True)로 목차 페이지를 "
            "이미지로 렌더하고 서버가 제공한 OCR 텍스트를 바탕으로 재구성. "
            "③ 직접 입력. ④ 청크. " + manual_chunk
        )
    else:  # legacy vision branch: 목차 이미지 OCR 경로
        reco["user_choices"] = ["proceed", "manual_pdf_pages", "chunks"]
        reco["next_step_guidance"] = (
            "[목차 OCR 분석] 내장 목차가 없습니다. **PDF 텍스트레이어나 파이썬 "
            "스크립트로 목차를 추정하지 마세요 — 스캔본·글꼴 깨진 PDF에서 잘못된 "
            "페이지가 나옵니다.** 응답의 toc_page_images[].ocr_text를 바탕으로 "
            "① 최상위 챕터 항목만(하위 절 '1.1' 무시) 골라 각 항목의 책 페이지번호를 "
            "읽고, ② toc_page_images[].path 이미지의 꼬리말 인쇄번호와 물리 페이지를 "
            "대조해 offset(물리 = 책 + offset)을 검증한 뒤 from_toc 챕터를 구성하세요"
            + (f" (서버 추정 offset {page_offset}은 참고만, 이미지로 검증). "
               if off_known else " (서버가 offset 미측정 → 이미지로 직접 추정). ")
            + "suggested_chapters는 비어 있습니다(서버는 OCR 텍스트를 제공하지만 "
            "챕터 경계를 자동 확정하지 않음). ocr_error가 있거나 OCR 텍스트만으로 "
            "부족하면 path 이미지를 확인하세요. chunk_fallback은 목차를 도저히 못 "
            "읽을 때만. 본문 언어도 파악해 set_chapters(language=\"ko\"|\"en\")로 "
            "전달하세요. "
            + excerpt_note + two_number + choices_policy
            + "① 이대로 진행 ② 직접 입력 ③ 청크. " + manual_chunk
        )
    return reco


def _build_recommendations(
    page_count: int,
    outline_chapters: list[dict[str, Any]] | None,
    page_offset: int | None = None,
    offset_confidence: str = "none",
) -> dict[str, Any]:
    """챕터 분리 추천을 만든다 — 내장 목차(outline) 우선, 없으면 목차 이미지 OCR.

    텍스트 레이어는 신뢰하지 않는다(스캔본·깨진 PDF에서 정렬이 깨져 잘못된
    목차가 나옴). 챕터 경계의 정당한 소스는 둘뿐:
      - outline_chapters 있음 → from_outline (북마크 = 물리 페이지 직접 지정).
        사용자 확인 후, 틀리면 force_vision=True로 목차 이미지 OCR 재스캔.
      - 없음 → analyze_toc_from_images (toc_page_images[].ocr_text 기반 구성).

    page_offset: 물리 = 인쇄(책) + offset. None이면 미측정.
    비거부 응답에는 _with_offset_meta로 page_offset/offset_confidence,
    각 챕터의 printed_range(책 페이지), user_choices, next_step_guidance가 붙는다.

    Returns:
        {
            "rejected": False,
            "primary_mode": "from_outline" | "analyze_toc_from_images",
            "primary_reason": str,
            "suggested_chapters": [{... "page_range":[s,e], "printed_range":[s,e]|None}],
            "chunk_fallback": [...],         # 이미지 OCR 경로에서만
            "alternatives": [str, ...],
            "page_offset": int | None,
            "offset_confidence": "high" | "low" | "none",
            "user_choices": [...],
            "next_step_guidance": str,
        }
    """
    if outline_chapters:
        return _with_offset_meta({
            "rejected": False,
            "reason": None,
            "primary_mode": "from_outline",
            "primary_reason": (
                f"PDF 내장 목차(북마크)에서 챕터 {len(outline_chapters)}개를 "
                "구성했습니다. 물리 페이지를 직접 가리켜 정확하나, 발췌본·오류 대비 "
                "사용자 확인을 받으세요(틀리면 force_vision=True로 목차 이미지 OCR 재스캔)."
            ),
            "suggested_chapters": outline_chapters,
            "alternatives": [
                "force_vision=True 로 목차 페이지를 렌더하고 OCR 텍스트 확인",
                f"chunks ({DEFAULT_CHUNK_SIZE}p 단위 균등 분할)",
            ],
        }, page_offset, offset_confidence, page_count, source="outline")

    # 내장 목차 없음 → 목차 페이지 이미지 + OCR 텍스트. 서버는 챕터를 제안하지
    # 않고, 에이전트가 toc_page_images[].ocr_text와 이미지를 확인해 from_toc를
    # 직접 구성한다. 청크는 '목차를 못 읽을 때만' 쓰는 최후 수단이라
    # suggested_chapters가 아니라 chunk_fallback에 둔다.
    return _with_offset_meta({
        "rejected": False,
        "reason": None,
        "primary_mode": "analyze_toc_from_images",
        "primary_reason": (
            "내장 목차가 없습니다. toc_page_images[].ocr_text를 바탕으로 챕터를 "
            "구성하세요(텍스트·스크립트 추정 금지). OCR 텍스트가 부족하거나 "
            "ocr_error가 있으면 path 이미지를 확인하세요. 목차를 도저히 못 읽을 때만 "
            "chunk_fallback(또는 single_unit)을 쓰세요."
        ),
        "suggested_chapters": [],  # 에이전트가 OCR 텍스트와 이미지를 확인해 채운다
        "chunk_fallback": chapter_mod.make_chunks(page_count, DEFAULT_CHUNK_SIZE),
        "alternatives": [
            f"chunk_fallback ({DEFAULT_CHUNK_SIZE}p 단위 균등 분할)",
            "single_unit (전체 1챕터)",
        ],
    }, page_offset, offset_confidence, page_count, source="vision")


def _attach_toc_ocr(toc_page_images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """렌더된 목차 페이지 이미지마다 PaddleOCR CPU 결과를 덧붙인다.

    OCR은 목차 경계의 보조 입력일 뿐 scan_pdf 전체 성공 여부를 결정하지 않는다.
    일부 페이지 실패는 해당 항목의 ocr_error에 남기고 나머지 페이지를 계속 처리한다.
    """
    try:
        worker = ocr.get_ocr_worker()
    except Exception as exc:
        logger.warning("toc OCR worker initialization failed: %s", exc)
        for item in toc_page_images:
            item["ocr_text"] = ""
            item["ocr_error"] = str(exc)
        return toc_page_images

    for item in toc_page_images:
        try:
            text = worker.process_image(item["path"])
        except Exception as exc:
            logger.warning("toc page OCR failed for %s: %s", item.get("path"), exc)
            item["ocr_text"] = ""
            item["ocr_error"] = str(exc)
            continue

        item["ocr_text"] = text
        item["ocr_error"] = None
    return toc_page_images


def _outline_to_chapters(
    outline: list[dict[str, Any]],
    page_count: int,
) -> list[dict[str, Any]]:
    """내장 목차(level,title,물리page) → set_chapters용 챕터(물리 page_range).

    최상위 레벨 항목만 챕터로 삼는다(하위 절은 제외). 각 챕터 끝은 다음
    최상위 챕터 시작−1, 마지막은 page_count. 북마크 page는 이미 물리 페이지라
    offset 보정이 필요 없다.

    **발췌본 처리**: 물리 시작이 page_count를 넘는 항목은 이 파일에 없는
    챕터이므로 **드롭**한다. 살아남은 마지막 챕터의 끝은 page_count로 둔다.
    """
    if not outline:
        return []
    min_level = min(e["level"] for e in outline)
    tops = sorted(
        (e for e in outline if e["level"] == min_level),
        key=lambda e: int(e["page"]),
    )
    tops = [e for e in tops if int(e["page"]) <= page_count]
    chapters: list[dict[str, Any]] = []
    for i, e in enumerate(tops):
        start = max(1, int(e["page"]))
        if i + 1 < len(tops):
            end = max(start, min(int(tops[i + 1]["page"]) - 1, page_count))
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
    force_vision: bool = False,
) -> dict[str, Any]:
    """PDF 스캔: 메타 + 챕터 경계 소스(내장 목차 또는 목차 페이지 OCR) + offset.

    챕터 경계 소스는 두 가지뿐이다(텍스트 레이어는 신뢰하지 않는다):
      (1) 내장 목차(doc.get_toc) → 물리 페이지를 직접 가리켜 정확·무비용.
      (2) 없으면 목차 페이지를 JPEG로 렌더(toc_page_images)하고 서버가
          PaddleOCR CPU 결과(ocr_text/ocr_error)를 함께 제공한다.
    force_vision=True는 외부 계약 호환용 legacy 이름이다. True면 (1)을 건너뛰고
    항상 (2)로 간다(내장 목차가 틀렸을 때).

    state.json에 language, page_count, page_offset을 채우고
    phases.scanning을 completed로 갱신한다. extraction_mode는 set_chapters에서
    정하므로 이 단계는 모드와 무관하다. scanned_text는 노출하지 않는다.
    """
    state = workspace.load_state(work_id)
    pdf_path = state["pdf_path"]

    workspace.update_phase(work_id, "scanning", "in_progress")

    doc = reader.open_pdf(pdf_path)
    try:
        page_count = doc.page_count
        book_metadata = reader.extract_metadata(doc)

        # 텍스트 레이어 품질 평가(mojibake 판정). scan_size(기본 30)p를 한 번 읽어
        # 그 sample_text를 언어 감지에 재사용한다 → 품질·언어가 같은 읽기를 공유.
        quality = reader.evaluate_text_quality(doc, scan_size)
        text_quality = quality["quality"]
        scan_end = min(scan_size, page_count) if page_count else 0

        if text_quality == "no_text_layer":
            # 텍스트 레이어가 없으면(스캔본) 언어·offset 측정이 무의미하다.
            # → 측정을 건너뛰어 불필요한 페이지 읽기(최대 page_count p 꼬리말 스캔 +
            #   언어 샘플)를 회피. 언어는 이후 set_chapters(ocr)에서 LLM이 이미지로
            #   파악해 전달하고, offset도 없음(none)으로 둔다.
            language = None
            offset_info = {"offset": None, "confidence": "none"}
        else:
            # 언어 감지: 품질 평가가 이미 읽은 샘플 텍스트 재사용 (재독 없음).
            language = lang.detect_language(quality["sample_text"])
            # 인쇄 페이지번호 ↔ PDF 물리 인덱스 오프셋 (꼬리말 번호 다수결)
            offset_info = reader.detect_page_offset(doc)

        # (1) 내장 목차 우선. force_vision이면 건너뛴다.
        outline = [] if force_vision else reader.get_outline(doc)
        outline_chapters = _outline_to_chapters(outline, page_count)
        use_outline = bool(outline_chapters)

        # (2) 내장 목차가 없으면 목차 페이지를 렌더하고 OCR 텍스트를 붙인다.
        toc_page_images: list[dict[str, Any]] = []
        if not use_outline and scan_end > 0:
            toc_pages = (
                reader.locate_toc_pages(doc, scan_size)
                or list(range(1, scan_end + 1))
            )
            toc_page_images = reader.render_pages(
                doc, toc_pages[0], toc_pages[-1], workspace.pages_dir(work_id),
            )
            _attach_toc_ocr(toc_page_images)
    finally:
        doc.close()

    page_offset = offset_info["offset"]
    offset_confidence = offset_info["confidence"]

    recommendations = _build_recommendations(
        page_count,
        outline_chapters if use_outline else None,
        page_offset=page_offset,
        offset_confidence=offset_confidence,
    )

    workspace.update_state(
        work_id,
        page_count=page_count,
        text_quality=text_quality,
        language=language,
        page_offset=page_offset,
        page_offset_confidence=offset_confidence,
    )
    workspace.update_phase(work_id, "scanning", "completed")

    workspace.save_outline(work_id, {
        "page_count": page_count,
        "text_quality": text_quality,
        "language": language,
        "page_offset": page_offset,
        "page_offset_confidence": offset_confidence,
        "outline_present": use_outline,
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
        "outline_present": use_outline,
        # 내장 목차가 없을 때만 채워진다 — 목차 페이지 이미지와 서버 OCR 결과
        "toc_page_images": toc_page_images,
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


def _ocr_chapter_pages(
    ch_def: dict[str, Any],
    page_images: list[dict[str, Any]],
) -> dict[str, Any]:
    """챕터 하나의 페이지 이미지를 순서대로 OCR해 raw payload를 만든다."""
    cid = ch_def["chapter_id"]
    worker = ocr.get_ocr_worker()
    page_texts: list[str] = []
    for page_image in page_images:
        image_path = page_image["path"]
        try:
            page_texts.append(str(worker.process_image(image_path) or ""))
        except Exception as exc:
            page = page_image.get("page")
            raise RuntimeError(f"chapter {cid} page {page} OCR failed: {exc}") from exc

    text = "\n\n".join(page_texts)
    if not text.strip():
        raise ValueError(f"chapter {cid} OCR produced empty text")

    return {
        "chapter_id": cid,
        "title": ch_def["title"],
        "page_range": ch_def["page_range"],
        "text": text,
        "char_count": len(text),
    }


def set_chapters_impl(
    work_id: str,
    chapters: list[dict[str, Any]],
    execution_mode: str,
    extraction_mode: str,
    book_info: dict[str, Any] | None = None,
    language: str = "",
) -> dict[str, Any]:
    """챕터 구조 확정 + 처리 모드 확정 → 챕터별 텍스트 추출 + 저장.

    Args:
        chapters: [{"chapter_id", "title", "page_range"=[start,end]}, ...]
                  (1-based inclusive)
        execution_mode: "sequential" | "parallel". 챕터 디스패치 방식.
        extraction_mode: "text" | "ocr". 본문 추출 방식(목차 단계와 무관).
                  text=라이브러리 추출 / ocr=PaddleOCR CPU 선계산.
        book_info: 메인 LLM이 보강한 책 정보. None이면 PDF 메타만 사용.
        language: "ko" | "en". OCR 모드에서 LLM이 이미지로 파악한 본문 언어를
                  전달하면 state.language를 갱신한다(텍스트 감지가 불가능하므로).

    Returns:
        {"chapter_count", "total_chars", "chapters": [...]}

    extraction_mode == "ocr"이면 set_chapters 시점에 페이지 이미지를 렌더하고
    PaddleOCR CPU로 본문 텍스트를 선계산해 raw에 저장한다.
    (그림 추출은 하지 않는다 — 요약은 텍스트/마크다운만 다룬다.)
    """
    if not chapters:
        raise ValueError("chapters must not be empty")
    if execution_mode not in workspace.VALID_EXECUTION_MODES:
        raise ValueError(
            f"execution_mode must be one of {workspace.VALID_EXECUTION_MODES}, "
            f"got {execution_mode!r}"
        )
    if extraction_mode not in workspace.VALID_EXTRACTION_MODES:
        raise ValueError(
            f"extraction_mode must be one of {workspace.VALID_EXTRACTION_MODES}, "
            f"got {extraction_mode!r}"
        )

    state = workspace.load_state(work_id)
    pdf_path = state["pdf_path"]
    # 처리 모드를 여기서 확정해 state에 기록 (init_work이 아니라 챕터 확정 시점).
    workspace.update_state(
        work_id, execution_mode=execution_mode, extraction_mode=extraction_mode,
    )
    ocr_mode = extraction_mode == "ocr"
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

    if ocr_mode:
        page_images_by_chapter: dict[str, list[dict[str, Any]]] = {}
        failed_chapters: set[str] = set()
        doc = reader.open_pdf(pdf_path)
        try:
            for ch_def in normalized:
                if ch_def.get("skip"):
                    continue
                cid = ch_def["chapter_id"]
                start, end = ch_def["page_range"]
                try:
                    page_images_by_chapter[cid] = reader.render_pages(
                        doc, int(start), int(end), workspace.pages_dir(work_id),
                    )
                except Exception as e:
                    logger.exception("chapter page rendering failed: %s", cid)
                    failed_chapters.add(cid)
                    workspace.mark_chapter_failed(
                        work_id, cid, kind="summary", error=str(e),
                    )
        finally:
            doc.close()

        results: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        max_workers = ocr.calculate_ocr_worker_limit()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chapter = {
                executor.submit(
                    _ocr_chapter_pages,
                    ch_def,
                    page_images_by_chapter[ch_def["chapter_id"]],
                ): ch_def
                for ch_def in normalized
                if not ch_def.get("skip")
                and ch_def["chapter_id"] in page_images_by_chapter
                and ch_def["chapter_id"] not in failed_chapters
            }
            for future in as_completed(future_to_chapter):
                ch_def = future_to_chapter[future]
                cid = ch_def["chapter_id"]
                try:
                    results[cid] = future.result()
                except Exception as e:
                    logger.exception("chapter OCR failed: %s", cid)
                    errors[cid] = str(e)
                    workspace.mark_chapter_failed(
                        work_id, cid, kind="summary", error=str(e),
                    )

        for ch_def in normalized:
            cid = ch_def["chapter_id"]
            if ch_def.get("skip"):
                summaries.append({
                    "chapter_id": cid, "title": ch_def["title"],
                    "page_range": ch_def["page_range"],
                    "char_count": 0,
                    "skipped": True, "error": None,
                })
                continue

            if cid in results:
                raw_payload = results[cid]
                char_count = raw_payload["char_count"]
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
                continue

            state_entry = workspace.load_state(work_id)["chapters"].get(cid, {})
            error = errors.get(cid) or state_entry.get("error") or "OCR failed"
            summaries.append({
                "chapter_id": cid, "title": ch_def["title"],
                "page_range": ch_def["page_range"],
                "char_count": 0, "error": error,
            })
    else:
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
                    "text": extracted["text"],
                }
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
    """챕터 raw 데이터 반환.

    - text 모드: chapters_raw의 {text}를 그대로 반환.
    - ocr 모드: set_chapters에서 선계산해 저장한 chapters_raw의 {text}를 그대로 반환.

    chapter_id는 **set_chapters로 등록된 id(ch1·ch2·…)**여야 한다. 페이지 범위
    같은 임의 문자열('p11-p18' 등)을 주면 등록 챕터 목록을 담아 거부한다 —
    특정 페이지를 그냥 보려면 scan_pdf의 toc_page_images 경로를 직접 열면 된다.
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
            "toc_page_images[].path 이미지를 직접 열어 읽으세요 — 이 도구로 "
            "페이지 범위를 가져오는 게 아닙니다.)"
        )

    if chapters[chapter_id].get("skip"):
        raise FileNotFoundError(
            f"chapter_id {chapter_id!r}는 skip(비본문: 표지·목차·색인 등)으로 "
            "표시돼 추출 대상이 아닙니다. 본문 챕터만 처리하세요."
        )

    return workspace.get_chapter_raw(work_id, chapter_id)  # 없으면 FileNotFoundError
