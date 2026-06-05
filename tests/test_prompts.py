"""prompts.build_prompts 단위 테스트."""
from __future__ import annotations

from pdf_study import prompts


def _state(**overrides):
    base = {
        "language": "ko",
        "execution_mode": "sequential",
        "question_options": {
            "multiple_choice": True,
            "short_answer": True,
            "reflection": True,
            "extension": True,
        },
        "user_context": "",
        "chapters": {"ch1": {}, "ch2": {}, "ch10": {}},
    }
    base.update(overrides)
    return base


def test_korean_template_used_when_language_ko():
    out = prompts.build_prompts(_state(language="ko"))
    assert out["language"] == "ko"
    assert "JSON 객체 하나만" in out["summarizer_prompt"]


def test_english_template_used_when_language_en():
    out = prompts.build_prompts(_state(language="en"))
    assert out["language"] == "en"
    assert "Return **exactly one JSON object**" in out["summarizer_prompt"]


def test_unknown_language_falls_back_to_en():
    out = prompts.build_prompts(_state(language="fr"))
    assert out["language"] == "en"


def test_extension_prompt_omitted_when_disabled():
    opts = {
        "multiple_choice": True, "short_answer": True,
        "reflection": True, "extension": False,
    }
    out = prompts.build_prompts(_state(question_options=opts))
    assert out["extension_prompt"] is None
    assert out["enabled_types"]["extension"] is False


def test_user_context_is_injected():
    out = prompts.build_prompts(_state(user_context="학부생 대상"))
    assert "학부생 대상" in out["summarizer_prompt"]


def test_book_info_fields_injected_into_prompt():
    out = prompts.build_prompts(_state(), book_info={
        "title": "테스트 책",
        "author": "샘플 저자",
        "publisher": "샘플 출판",
        "preface_summary": "이 책은 데이터베이스 개론서다.",
    })
    p = out["summarizer_prompt"]
    assert "테스트 책" in p
    assert "샘플 저자" in p
    assert "샘플 출판" in p
    assert "데이터베이스 개론서" in p


def test_workflow_instructions_branch_by_mode():
    seq = prompts.build_prompts(_state(execution_mode="sequential"))
    par = prompts.build_prompts(_state(execution_mode="parallel"))
    assert "한 챕터씩 처리하세요" in seq["workflow_instructions"]
    assert "최대 5개 챕터를 동시에" in par["workflow_instructions"]


def test_text_mode_input_block_default():
    """기본(text) 모드는 본문 text 입력 블록을 쓴다."""
    out = prompts.build_prompts(_state())
    assert out["extraction_mode"] == "text"
    assert "[입력 방식 — 본문 텍스트]" in out["summarizer_prompt"]
    assert "[OCR 모드]" not in out["workflow_instructions"]


def test_ocr_mode_uses_page_image_input_block_and_workflow_note():
    """ocr 모드는 page_images 입력 블록 + 워크플로 OCR 노트를 쓴다."""
    out = prompts.build_prompts(_state(extraction_mode="ocr"))
    assert out["extraction_mode"] == "ocr"
    p = out["summarizer_prompt"]
    assert "[입력 방식 — 페이지 이미지(OCR)]" in p
    assert "page_images" in p
    assert "[입력 방식 — 본문 텍스트]" not in p
    assert "[OCR 모드]" in out["workflow_instructions"]


def test_ocr_mode_english_input_block():
    out = prompts.build_prompts(_state(language="en", extraction_mode="ocr"))
    assert "[Input mode — page images (OCR)]" in out["summarizer_prompt"]


def test_chapter_ids_naturally_sorted():
    out = prompts.build_prompts(_state())
    # ch1, ch2, ch10이 사전순(ch1, ch10, ch2)이 아니라 자연수 순으로
    assert out["chapter_ids"] == ["ch1", "ch2", "ch10"]


def test_summary_length_table_present_per_language():
    """챕터 본문 분량에 따른 요약 길이 권장 표가 KO/EN 모두에 들어가야 한다."""
    ko = prompts.build_prompts(_state(language="ko"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en"))["summarizer_prompt"]
    # KO 표 — 본문의 약 1/3 기준
    assert "본문 글자수의 약 1/3" in ko
    assert "800–1,200자" in ko and "6,000–10,000자" in ko
    # EN 표
    assert "1/3 of the body length" in en
    assert "800–1,200 chars" in en and "6,000–10,000 chars" in en


def test_summary_length_is_not_fixed_400_800():
    """예전 고정 한도(400–800자)가 사용자에게 강제되지 않아야 한다."""
    ko = prompts.build_prompts(_state(language="ko"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en"))["summarizer_prompt"]
    # JSON 스키마 안의 summary 필드 안내가 본문 분량 기반인지 확인
    assert "400–800자 한국어 요약" not in ko
    assert "400–800 char English summary" not in en
    assert "본문 분량에 맞춘" in ko
    assert "scaled to body size" in en


def test_enabled_types_reflects_options():
    opts = {
        "multiple_choice": True, "short_answer": False,
        "reflection": True, "extension": False,
    }
    out = prompts.build_prompts(_state(question_options=opts))
    assert out["enabled_types"] == {
        "multiple_choice": True, "short_answer": False,
        "reflection": True, "extension": False,
    }
