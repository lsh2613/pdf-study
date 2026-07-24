"""`set_chapters` 처리 모드 선택지의 단일 내부 계약.

공개 응답에는 execution_mode, extraction_mode, label, desc만 노출한다.
오류 안내용 세부 문구와 선택지 번호는 이 모듈 안에서만 관리한다.
"""
from __future__ import annotations

from typing import Any


VALID_EXECUTION_MODES = ("sequential", "parallel")
VALID_EXTRACTION_MODES = ("text", "ocr")
_TEXT_UNAVAILABLE_QUALITIES = frozenset({"garbled", "no_text_layer"})

_EXTRACTION_SPECS = (
    {
        "value": "text",
        "label": "Text",
        "desc": "PDF 텍스트 레이어를 사용해 본문을 추출합니다.",
    },
    {
        "value": "ocr",
        "label": "OCR",
        "desc": "PaddleOCR CPU로 본문을 먼저 읽어 텍스트로 저장합니다.",
    },
)

_EXECUTION_SPECS = (
    {
        "value": "sequential",
        "label": "Sequential",
        "desc": "챕터를 한 개씩 순서대로 처리합니다.",
    },
    {
        "value": "parallel",
        "label": "Parallel",
        "desc": "최대 5개 sub-agent가 챕터를 동시에 처리합니다.",
    },
)

_MODE_SPECS = (
    {
        "number": "①",
        "execution_mode": "sequential",
        "extraction_mode": "text",
        "label": "Sequential + Text",
        "desc": "디지털 PDF · 안정적·빠르고 저렴",
        "error_desc": "한 챕터씩 순차 + 라이브러리 텍스트 추출. 안정적·빠르고 저렴 (디지털 PDF).",
    },
    {
        "number": "②",
        "execution_mode": "parallel",
        "extraction_mode": "text",
        "label": "Parallel + Text",
        "desc": "디지털 PDF · 최대 5개 동시로 가장 빠름",
        "error_desc": "최대 5개 챕터 동시 + 텍스트 추출. 가장 빠름 (병렬 디스패치 가능한 클라이언트).",
    },
    {
        "number": "③",
        "execution_mode": "sequential",
        "extraction_mode": "ocr",
        "label": "Sequential + OCR",
        "desc": "스캔본·깨진 PDF · PaddleOCR CPU 선계산 뒤 순차 sub-agent 처리",
        "error_desc": "PaddleOCR CPU 선계산 뒤 순차 sub-agent 처리.",
    },
    {
        "number": "④",
        "execution_mode": "parallel",
        "extraction_mode": "ocr",
        "label": "Parallel + OCR",
        "desc": "스캔본·깨진 PDF · PaddleOCR CPU 선계산 뒤 최대 5개 sub-agent 동시 처리",
        "error_desc": "PaddleOCR CPU 선계산 뒤 최대 5개 sub-agent 동시 처리.",
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


def choices(text_quality: str | None) -> list[dict[str, str]]:
    """텍스트 품질에 맞는 공개 처리 모드 선택지의 새 복사본을 반환한다."""
    specs = _MODE_SPECS
    if text_extraction_is_unavailable(text_quality):
        specs = tuple(spec for spec in specs if spec["extraction_mode"] == "ocr")
    return [
        {
            "execution_mode": spec["execution_mode"],
            "extraction_mode": spec["extraction_mode"],
            "label": spec["label"],
            "desc": spec["desc"],
        }
        for spec in specs
    ]


def set_chapters_next_step(text_quality: str | None) -> dict[str, Any]:
    return {
        "tool": "set_chapters",
        "required_parameters": ["chapters", "execution_mode", "extraction_mode"],
        "choices": choices(text_quality),
        "user_choice_required": True,
        "user_choice_instruction": (
            "choices의 모든 항목과 설명을 그대로 사용자에게 보여주고, 반드시 사용자에게서 받은 "
            "선택값 중 execution_mode와 extraction_mode만 다음 도구에 전달하세요."
        ),
    }


def invalid_mode_message(text_quality: str | None) -> str:
    """기존 fallback 오류의 설명을 canonical 선택지 정의에서 조합한다."""
    if text_extraction_is_unavailable(text_quality):
        reason = (
            "텍스트 레이어 인코딩이 깨져 있어(mojibake)"
            if text_quality == "garbled"
            else "텍스트 레이어가 거의 없어"
        )
        lines = _error_lines(extraction_mode="ocr")
        return (
            f"이 PDF는 {reason} text 추출이 무의미합니다(text_quality={text_quality}). "
            "따라서 **OCR 조합만 선택할 수 있습니다** — text 조합은 제시하지 마세요. "
            "아래 2가지 중 하나를 사용자에게 보여주고 골라 두 값을 전달해 다시 "
            "호출하세요.\n"
            f"{lines}"
            "OCR 선처리는 execution_mode와 별개로 서버 내부 상한(최대 2개 챕터, "
            "CPU 1코어면 1개)으로 제한됩니다. extraction_mode는 'ocr' 고정, "
            "execution_mode만 'sequential'|'parallel'에서 선택.\n"
        )
    return (
        "execution_mode와 extraction_mode를 모두 지정해야 합니다. 기본값을 "
        "임의로 정하지 말고, 아래 4가지 조합과 특징을 사용자에게 그대로 보여준 뒤 "
        "원하는 하나를 골라 두 값을 전달해 다시 호출하세요. 4개 모두 유효하니 "
        "임의로 빼지 말고 전부 제시할 것.\n"
        f"{_error_lines()}"
        "OCR 선처리는 execution_mode와 별개로 서버 내부 상한(최대 2개 챕터, CPU 1코어면 1개)으로 "
        "별도 제한됩니다.\n"
        "execution_mode는 'sequential'|'parallel', extraction_mode는 'text'|'ocr'.\n"
    )


def invalid_mode_data(text_quality: str | None) -> dict[str, Any]:
    """기존 fallback `data`의 키와 값 형태를 유지한다."""
    force_ocr = text_extraction_is_unavailable(text_quality)
    data: dict[str, Any] = {
        "choices": choices(text_quality),
        "user_choice_required": True,
        "user_choice_instruction": (
            "choices의 모든 항목과 설명을 그대로 사용자에게 보여주고, 반드시 사용자에게서 받은 "
            "선택값 중 execution_mode와 extraction_mode만 다음 도구에 전달하세요."
        ),
        "execution_modes": list(VALID_EXECUTION_MODES),
        "extraction_modes": ["ocr"] if force_ocr else list(VALID_EXTRACTION_MODES),
    }
    if force_ocr:
        data.update({
            "text_quality": text_quality,
            "forced_extraction_mode": "ocr",
        })
    return data


def _error_lines(extraction_mode: str | None = None) -> str:
    return "".join(
        f'{spec["number"]} {spec["label"]} — {spec["error_desc"]}\n'
        for spec in _MODE_SPECS
        if extraction_mode is None or spec["extraction_mode"] == extraction_mode
    )
