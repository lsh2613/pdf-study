"""PyMuPDF 래퍼: PDF 열기, 메타/페이지 텍스트 추출, 품질 평가.

페이지 인덱스 컨벤션:
    외부 API(이 모듈의 공개 함수 인자/반환값)는 모두 **1-based**.
    PyMuPDF는 내부적으로 0-based이며, 변환은 이 모듈 안에서만 수행한다.
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)


_BROKEN_UNICODE_RE = re.compile(r"�+")
_MULTI_WS_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_PAGE_NUM_LINE_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")

# 모지바케(인코딩 깨짐) 감지 파라미터
_MOJIBAKE_THRESHOLD = 0.06   # 샘플 평균 점수가 이보다 크면 "garbled"
_MOJIBAKE_MIN_CHARS = 100    # 점수 산정에 필요한 페이지 최소 글자 수
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))
_SYMBOL_WHITELIST = set("%℃°±×÷=")  # 본문에 정상적으로 흔한 기호


def _is_hangul(c: str) -> bool:
    o = ord(c)
    return 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def _is_latin_alpha(c: str) -> bool:
    return c.isascii() and c.isalpha()


def _is_noise_symbol(c: str) -> bool:
    """본문에 드문 기호(So/Sm/Sk/Sc). 흔한 단위·연산 기호는 제외."""
    return unicodedata.category(c)[0] == "S" and c not in _SYMBOL_WHITELIST


def _in_pua(c: str) -> bool:
    o = ord(c)
    return any(lo <= o <= hi for lo, hi in _PUA_RANGES)


def mojibake_score(text: str) -> float:
    """인코딩 깨짐(모지바케) 정도를 0.0(정상)~1.0(깨짐)으로 추정.

    글꼴 ToUnicode 매핑이 손상돼 글리프가 엉뚱한 코드포인트로 추출되면
    세 신호가 비정상적으로 높아진다:
        (1) 사용자영역(PUA) · U+FFFD 문자 비율
        (2) **스크립트 혼합 경계** — 한글과 라틴 글자, 또는 글자와 잡기호가
            공백 없이 붙는 경계 비율 (예: "b"VhWD갈돌개", "正골임숫돌퀀…zQ").
            숫자↔쉼표 같은 정상 수치 표기는 제외해 표 데이터 오탐을 막는다.
        (3) 잡기호 밀집 비율
    셋을 합성한다. 표본이 부족하면(<100자) 0.0을 반환해 오탐을 피한다.

    주의: 깨진 글자가 '정상 유니코드 한글 음절'로 매핑되는 경우가 많아
    (예: "용석눈힎개") 코드포인트 유효성만으로는 잡히지 않으므로, 토큰
    내부의 비정상적 스크립트 혼합이라는 구조적 신호를 본다.
    """
    chars = [c for c in text if not c.isspace()]
    n = len(chars)
    if n < _MOJIBAKE_MIN_CHARS:
        return 0.0

    pua = sum(1 for c in chars if _in_pua(c) or c == "�")
    sym = sum(1 for c in chars if _is_noise_symbol(c))

    mixing = 0
    for a, b in zip(text, text[1:]):
        if a.isspace() or b.isspace():
            continue
        ha, hb = _is_hangul(a), _is_hangul(b)
        la, lb = _is_latin_alpha(a), _is_latin_alpha(b)
        if (ha and lb) or (hb and la):            # 한글↔라틴 글자 붙음
            mixing += 1
        elif (ha and _is_noise_symbol(b)) or (hb and _is_noise_symbol(a)):
            mixing += 1                            # 한글↔잡기호 붙음
        elif (la and _is_noise_symbol(b)) or (lb and _is_noise_symbol(a)):
            mixing += 1                            # 라틴↔잡기호 붙음

    return (mixing + pua + 0.5 * sym) / n


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


def get_outline(doc: fitz.Document) -> list[dict[str, Any]]:
    """PDF 내장 목차(북마크) 추출.

    doc.get_toc()는 [[level, title, page(1-based 물리)], ...]을 돌려준다.
    북마크는 **물리 페이지를 직접** 가리키므로 offset 보정·OCR 없이 정확하다.
    내장 목차가 있으면 이게 챕터 경계의 1순위 소스(무비용).

    Returns:
        [{"level": int, "title": str, "page": int}, ...]  (page는 1-based 물리)
        유효 항목이 없으면 빈 리스트.
    """
    try:
        raw = doc.get_toc(simple=True)
    except Exception as e:  # pragma: no cover - 손상된 PDF 방어
        logger.warning("get_toc failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if len(entry) < 3:
            continue
        level, title, page = entry[0], entry[1], entry[2]
        title = (title or "").strip()
        if page is None or int(page) < 1 or not title:
            continue
        out.append({"level": int(level), "title": title, "page": int(page)})
    return out


_TOC_KEYWORDS = ("목차", "차례", "contents", "table of contents")


def locate_toc_pages(doc: fitz.Document, max_scan: int = 30) -> list[int] | None:
    """인쇄된 목차 페이지의 **위치**(1-based)를 best-effort로 찾는다.

    텍스트가 깨졌어도 '목차/Contents' 키워드는 대개 살아남으므로, 페이지 번호
    (숫자)가 아니라 '어느 페이지가 목차인가'만 찾는 용도다. 실제 챕터↔페이지
    숫자 추출은 vision(에이전트)이 toc_page_images를 읽어서 한다.

    Returns:
        목차로 보이는 페이지 번호 리스트(1-based, 연속). 못 찾으면 None.
    """
    n = min(max_scan, doc.page_count)
    hits: list[int] = []
    for p in range(1, n + 1):
        try:
            text = doc.load_page(p - 1).get_text("text").lower()
        except Exception:  # pragma: no cover
            continue
        if any(kw in text for kw in _TOC_KEYWORDS):
            hits.append(p)
    if not hits:
        return None
    # 목차가 여러 장에 걸칠 수 있으나, 키워드 오검출로 과도하게 넓어지는 것을
    # 막기 위해 첫 hit부터 최대 6장으로 제한.
    start = hits[0]
    end = min(hits[-1], start + 5)
    return list(range(start, end + 1))


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
            "quality": "high" | "medium" | "low" | "no_text_layer" | "garbled",
            "avg_chars_per_page": float,
            "avg_mojibake": float,
            "sampled_pages": int,
        }

    임계값:
        no_text_layer: avg_chars < 50  (OCR 권장)
        garbled:       avg_mojibake > 0.06  (인코딩 깨짐 — OCR 또는 무손실 재추출)
        low:           50 <= avg_chars < 300
        medium:        300 <= avg_chars < 800
        high:          avg_chars >= 800
    """
    n = min(sample_size, doc.page_count)
    if n == 0:
        return {
            "quality": "no_text_layer",
            "avg_chars_per_page": 0.0,
            "avg_mojibake": 0.0,
            "sampled_pages": 0,
        }

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
    mojibake_scores: list[float] = []
    for idx in sample_indices:
        try:
            raw = doc.load_page(idx).get_text("text")
        except Exception as e:
            logger.warning("page %d text extraction failed: %s", idx, e)
            continue
        total_chars += len(_clean_text(raw))
        # 모지바케 점수는 _clean_text 전 raw로 — U+FFFD 등 신호 보존.
        # 표본이 충분한 페이지만 평균에 반영해 빈 페이지의 0점 희석 방지.
        if sum(1 for c in raw if not c.isspace()) >= _MOJIBAKE_MIN_CHARS:
            mojibake_scores.append(mojibake_score(raw))

    avg = total_chars / len(sample_indices)
    avg_mojibake = (
        sum(mojibake_scores) / len(mojibake_scores) if mojibake_scores else 0.0
    )

    if avg < 50:
        quality = "no_text_layer"
    elif avg_mojibake > _MOJIBAKE_THRESHOLD:
        quality = "garbled"
    elif avg < 300:
        quality = "low"
    elif avg < 800:
        quality = "medium"
    else:
        quality = "high"

    return {
        "quality": quality,
        "avg_chars_per_page": round(avg, 1),
        "avg_mojibake": round(avg_mojibake, 3),
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


# ---------------------------------------------------------------------------
# 페이지 오프셋 측정 (인쇄 페이지번호 ↔ PDF 물리 인덱스)
# ---------------------------------------------------------------------------

_FOOTER_NUM_RE = re.compile(r"^(\d{1,4})$")  # 꼬리말의 숫자-only 줄
_OFFSET_FOOTER_LINES = 3       # 페이지 끝 몇 줄까지 인쇄번호로 볼지
_OFFSET_MIN_SUPPORT = 3        # 최빈 offset 최소 지지 표 수
_OFFSET_DOMINANCE = 2          # 최빈이 2등의 N배 이상이어야 high


def _footer_printed_number(raw: str) -> int | None:
    """raw 페이지 텍스트의 꼬리말에서 인쇄 페이지번호(숫자-only 줄)를 추출.

    raw(=get_text 원본)를 받아야 한다. extract_page_text는 _PAGE_NUM_LINE_RE로
    숫자-only 줄을 이미 제거하므로 여기 쓰면 안 된다.
    """
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return None
    for ln in reversed(lines[-_OFFSET_FOOTER_LINES:]):
        m = _FOOTER_NUM_RE.match(ln)
        if m:
            p = int(m.group(1))
            if 1 <= p <= 5000:
                return p
    return None


def detect_page_offset(doc: fitz.Document, sample_cap: int = 400) -> dict[str, Any]:
    """인쇄 페이지번호 ↔ PDF 물리 인덱스의 오프셋을 추정.

    각 페이지 꼬리말의 인쇄번호를 읽어 candidate = (물리인덱스 − 인쇄번호)를
    모으고, 최빈값(mode)을 오프셋으로 본다. 빈 페이지·번호 없는 표지·도입부는
    자연히 후보에서 빠지고, 본문 노이즈(코드 줄번호 등)는 단발이라 최빈에 밀린다.

    오프셋은 음수일 수 있다(PDF가 앞 front matter를 일부 누락한 경우).
    텍스트 레이어가 없거나 인쇄번호가 전혀 없으면 offset=None/none.

    Returns:
        {
            "offset": int | None,      # 물리 = 인쇄 + offset
            "confidence": "high" | "low" | "none",
            "support": int,            # 최빈 offset 지지 표 수
            "samples": int,            # 인쇄번호를 읽은 페이지 수
            "runner_up": int,          # 2등 표 수 (참고)
        }
    """
    n = doc.page_count
    if n == 0:
        return {"offset": None, "confidence": "none", "support": 0,
                "samples": 0, "runner_up": 0}

    # 페이지가 매우 많으면 균등 표본만 (대용량 PDF 보호)
    if n <= sample_cap:
        indices = range(n)
    else:
        step = n / sample_cap
        indices = sorted({int(i * step) for i in range(sample_cap)})

    counts: dict[int, int] = {}
    samples = 0
    for idx in indices:
        raw = doc.load_page(idx).get_text("text")
        printed = _footer_printed_number(raw)
        if printed is None:
            continue
        samples += 1
        cand = (idx + 1) - printed  # 물리(1-based) − 인쇄
        counts[cand] = counts.get(cand, 0) + 1

    if not counts:
        return {"offset": None, "confidence": "none", "support": 0,
                "samples": 0, "runner_up": 0}

    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    offset, support = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0

    if support >= _OFFSET_MIN_SUPPORT and support >= _OFFSET_DOMINANCE * max(runner_up, 1):
        confidence = "high"
    else:
        confidence = "low"

    return {
        "offset": offset,
        "confidence": confidence,
        "support": support,
        "samples": samples,
        "runner_up": runner_up,
    }


# ---------------------------------------------------------------------------
# 페이지 → JPEG 렌더 (OCR 모드: 비전 LLM이 페이지를 직접 읽게 함)
# ---------------------------------------------------------------------------

RENDER_DPI = 150       # 한글 본문 가독 충분 + 비전 토큰 절약
RENDER_QUALITY = 80    # JPEG 품질 (스캔 텍스트는 PNG보다 3~5배 작음)


def render_pages(
    doc: fitz.Document,
    start: int,
    end: int,
    output_dir: str | Path,
    dpi: int = RENDER_DPI,
    quality: int = RENDER_QUALITY,
) -> list[dict[str, Any]]:
    """페이지 범위(1-based inclusive)를 JPEG로 렌더해 output_dir에 저장.

    OCR 모드에서 서브에이전트(비전 LLM)가 본문 텍스트 대신 페이지 이미지를
    직접 읽도록 하기 위함. 파일명은 p{N}.jpg로 페이지 단위라, scan과 챕터가
    같은 페이지를 렌더해도 한 번만 만들어 캐시처럼 재사용한다(이미 있으면 스킵).

    Returns:
        [{"id": "p12", "path": "<output_dir>/p12.jpg", "page": 12}, ...]
        path는 절대 경로 문자열(호출자가 받은 output_dir 기준).
    """
    if start < 1 or end > doc.page_count or start > end:
        raise ValueError(
            f"invalid page range [{start}, {end}] for {doc.page_count}p doc"
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    refs: list[dict[str, Any]] = []
    for p in range(start, end + 1):
        out_path = out_dir / f"p{p}.jpg"
        if not out_path.exists():
            pix = doc.load_page(p - 1).get_pixmap(dpi=dpi)  # 0-based 경계 변환
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(out_path, format="JPEG", quality=quality, optimize=True)
        refs.append({"id": f"p{p}", "path": str(out_path), "page": p})
    return refs
