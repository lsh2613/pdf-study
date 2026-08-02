"""`set_chapters` Elicitation 처리 모드의 단일 내부 계약."""
from __future__ import annotations

from typing import Any


VALID_EXECUTION_MODES = ("sequential", "parallel")
VALID_EXTRACTION_MODES = ("text", "ocr")
_TEXT_UNAVAILABLE_QUALITIES = frozenset({"garbled", "no_text_layer"})

_EXTRACTION_SPECS = (
    {
        "value": "text",
        "label": "PyMuPDF",
        "desc": "PDF 텍스트 레이어에서 본문을 직접 추출",
    },
    {
        "value": "ocr",
        "label": "PyMuPDF + PaddleOCR",
        "desc": "PDF 페이지를 이미지로 렌더링한 뒤 OCR로 본문 추출",
    },
)

_EXECUTION_SPECS = (
    {
        "value": "sequential",
        "label": "순차 처리",
        "desc": "",
    },
    {
        "value": "parallel",
        "label": "병렬 처리",
        "desc": "최대 5개 챕터 동시 처리",
    },
)

def text_extraction_is_unavailable(text_quality: str | None) -> bool:
    return text_quality in _TEXT_UNAVAILABLE_QUALITIES


def extraction_choices(text_quality: str | None) -> list[dict[str, str]]:
    """본문 추출 방식 elicitation의 독립 선택지를 반환한다."""
    specs = _EXTRACTION_SPECS
    if text_extraction_is_unavailable(text_quality):
        specs = tuple(spec for spec in specs if spec["value"] == "ocr")
    return [dict(spec) for spec in specs]


def execution_choices() -> list[dict[str, str]]:
    """챕터 실행 방식 elicitation의 독립 선택지를 반환한다."""
    return [dict(spec) for spec in _EXECUTION_SPECS]


def set_chapters_next_step(text_quality: str | None) -> dict[str, Any]:
    return {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }


def invalid_mode_message(text_quality: str | None) -> str:
    """공개 MCP wrapper 밖에서만 발생 가능한 내부 불변식 오류."""
    suffix = f" (text_quality={text_quality})" if text_quality else ""
    return f"내부 처리 모드가 확정되지 않았습니다{suffix}."


def invalid_mode_data(text_quality: str | None) -> dict[str, Any]:
    return {"text_quality": text_quality} if text_quality else {}
