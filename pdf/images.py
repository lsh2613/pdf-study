"""챕터별 이미지 추출 → PNG 저장.

너무 작은 이미지(아이콘, 디바이더 등)는 거른다. 최종 리사이즈는 LLM
멀티모달 입력 부담을 줄이기 위해 긴 변 기준 1600px로 제한.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

logger = logging.getLogger(__name__)

MIN_IMAGE_DIM = 80           # 너무 작은 이미지 거름
MAX_LONG_EDGE = 1600         # 긴 변 기준 다운스케일 한도
PNG_OPTIMIZE = True
# 페이지 면적의 이 비율 이상을 차지하는 이미지는 "페이지 전체 배경"으로
# 간주하고 거른다. 스캔본 PDF가 텍스트 레이어 + 페이지 raster를 함께
# 갖는 경우(이 책처럼) 페이지마다 같은 풀페이지 이미지가 잡혀 들어온다.
FULL_PAGE_AREA_RATIO = 0.7


def _save_png(img_bytes: bytes, out_path: Path) -> tuple[int, int] | None:
    """바이트를 PIL로 열고, 필요 시 리사이즈 후 PNG로 저장. 실패 시 None."""
    try:
        img = Image.open(io.BytesIO(img_bytes))
        img.load()
    except Exception as e:
        logger.warning("PIL open failed for %s: %s", out_path.name, e)
        return None

    w, h = img.size
    if w < MIN_IMAGE_DIM or h < MIN_IMAGE_DIM:
        return None

    long_edge = max(w, h)
    if long_edge > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / long_edge
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.LANCZOS)
        w, h = new_size

    # PNG는 RGBA/RGB/L 정도만 안전. 그 외는 RGB로 컨버트.
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")

    img.save(out_path, format="PNG", optimize=PNG_OPTIMIZE)
    return w, h


def extract_chapter_images(
    doc: fitz.Document,
    chapter_id: str,
    page_range: list[int] | tuple[int, int],
    output_dir: str | Path,
) -> list[dict[str, Any]]:
    """챕터 페이지 범위(1-based inclusive)의 이미지를 PNG로 저장.

    Returns:
        [
            {"id": "ch1_p23_0", "path": "<output_dir>/ch1_p23_0.png", "page": 23},
            ...
        ]
        path는 호출자가 받은 output_dir 기준 절대 경로(또는 그대로 Path str).
    """
    if len(page_range) != 2:
        raise ValueError(f"page_range must be [start, end], got {page_range}")
    start, end = int(page_range[0]), int(page_range[1])
    if start < 1 or end > doc.page_count or start > end:
        raise ValueError(
            f"invalid page_range [{start}, {end}] for {doc.page_count}p doc"
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    refs: list[dict[str, Any]] = []
    seen_xrefs: set[int] = set()  # 같은 이미지가 여러 페이지에 박힐 때 중복 방지

    for page_no_1based in range(start, end + 1):
        page = doc.load_page(page_no_1based - 1)  # 0-based 경계 변환
        try:
            image_list = page.get_images(full=True)
        except Exception as e:
            logger.warning("get_images failed on page %d: %s", page_no_1based, e)
            continue

        # 페이지 안에서 각 이미지가 차지하는 bbox 조회용 (xref → bbox 리스트)
        page_area = page.rect.get_area()
        bbox_by_xref: dict[int, list[float]] = {}
        try:
            for info in page.get_image_info(xrefs=True):
                xref_key = info.get("xref")
                bbox = info.get("bbox")
                if xref_key is None or bbox is None:
                    continue
                rect = fitz.Rect(bbox)
                bbox_by_xref.setdefault(xref_key, []).append(rect.get_area())
        except Exception as e:
            logger.debug("get_image_info failed on page %d: %s", page_no_1based, e)

        for img_idx, img_info in enumerate(image_list):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            # 페이지 전체 배경 이미지 거름
            if page_area > 0:
                max_ratio = max(
                    (a / page_area for a in bbox_by_xref.get(xref, [])),
                    default=0.0,
                )
                if max_ratio >= FULL_PAGE_AREA_RATIO:
                    logger.debug(
                        "skip full-page image: page=%d xref=%d ratio=%.2f",
                        page_no_1based, xref, max_ratio,
                    )
                    continue

            try:
                extracted = doc.extract_image(xref)
            except Exception as e:
                logger.warning(
                    "extract_image(xref=%d) failed on page %d: %s",
                    xref, page_no_1based, e,
                )
                continue

            img_bytes = extracted.get("image")
            if not img_bytes:
                continue

            img_id = f"{chapter_id}_p{page_no_1based}_{img_idx}"
            out_path = out_dir / f"{img_id}.png"

            size = _save_png(img_bytes, out_path)
            if size is None:
                # 너무 작거나 디코딩 실패: 파일이 만들어졌으면 정리
                if out_path.exists():
                    out_path.unlink()
                continue

            refs.append({
                "id": img_id,
                "path": str(out_path),
                "page": page_no_1based,
            })

    return refs
