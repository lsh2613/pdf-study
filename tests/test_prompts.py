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


def test_ocr_mode_uses_precomputed_text_input_block_and_workflow_note():
    """ocr 모드는 선계산 text 입력 블록 + 워크플로 OCR 노트를 쓴다."""
    out = prompts.build_prompts(_state(extraction_mode="ocr"))
    assert out["extraction_mode"] == "ocr"
    p = out["summarizer_prompt"]
    assert "[입력 방식 — OCR 선계산 본문 텍스트]" in p
    assert "get_chapter_content가 제공한 text" in p
    assert "page_images" not in p
    assert "[입력 방식 — 본문 텍스트]" not in p
    assert "[OCR 모드]" in out["workflow_instructions"]
    assert "본문 text" in out["workflow_instructions"]


def test_ocr_mode_english_input_block():
    out = prompts.build_prompts(_state(language="en", extraction_mode="ocr"))
    assert "[Input mode — precomputed OCR text]" in out["summarizer_prompt"]


def test_chapter_ids_naturally_sorted():
    out = prompts.build_prompts(_state())
    # ch1, ch2, ch10이 사전순(ch1, ch10, ch2)이 아니라 자연수 순으로
    assert out["chapter_ids"] == ["ch1", "ch2", "ch10"]


def test_summary_length_table_removed_per_language():
    """요약 길이는 본문 글자 수 기반 표로 산정하지 않는다."""
    ko = prompts.build_prompts(_state(language="ko"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en"))["summarizer_prompt"]
    assert "챕터 본문 분량에 따른 요약 길이 권장" not in ko
    assert "본문 글자수의 약 1/3" not in ko
    assert "800–1,200자" not in ko and "6,000–10,000자" not in ko
    assert "Suggested summary length per chapter body size" not in en
    assert "1/3 of the body length" not in en
    assert "800–1,200 chars" not in en and "6,000–10,000 chars" not in en


def test_summary_schema_does_not_scale_to_body_size():
    """summary 필드 안내는 본문 분량 기반 길이 산정을 요구하지 않는다."""
    ko = prompts.build_prompts(_state(language="ko"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en"))["summarizer_prompt"]
    assert "400–800자 한국어 요약" not in ko
    assert "400–800 char English summary" not in en
    assert "본문 분량에 맞춘" not in ko
    assert "scaled to body size" not in en


def test_summary_format_requires_subchapter_sections():
    """summary는 3.1/3.2 같은 서브 챕터 단위로 구조화되어야 한다."""
    ko = prompts.build_prompts(_state(language="ko"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en"))["summarizer_prompt"]
    assert "3.1" in ko and "3.2" in ko
    assert "서브 챕터" in ko
    assert "각 서브 챕터마다" in ko
    assert "3.1" in en and "3.2" in en
    assert "subchapter" in en
    assert "one section per subchapter" in en


def test_ocr_mode_only_counts_chars_for_question_counts():
    """OCR에서 읽어낸 글자 수는 문제 개수 산정에만 쓰고 요약 길이에 쓰지 않는다."""
    ko = prompts.build_prompts(_state(language="ko", extraction_mode="ocr"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en", extraction_mode="ocr"))["summarizer_prompt"]
    assert "문제 개수" in ko
    assert "요약 길이" not in ko
    assert "question counts" in en
    assert "summary length" not in en


def test_summary_format_markdown_no_images():
    """요약은 마크다운으로 작성하되 이미지(그림)는 넣지 말라는 지시."""
    ko = prompts.build_prompts(_state(language="ko"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en"))["summarizer_prompt"]
    assert "[요약 작성 형식 — 마크다운]" in ko
    assert "이미지(그림)는 넣지 마세요" in ko
    assert "fig:" not in ko                       # 그림 인라인 토큰 흔적 없음
    assert "[Summary format — Markdown]" in en
    assert "Do not embed images" in en
    assert "fig:" not in en


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


def test_question_counts_are_maximums_not_targets():
    """문제 개수 표는 채워야 하는 목표치가 아니라 품질 기준 상한이다."""
    ko = prompts.build_prompts(_state(language="ko"))["summarizer_prompt"]
    en = prompts.build_prompts(_state(language="en"))["summarizer_prompt"]
    ext_ko = prompts.build_prompts(_state(language="ko"))["extension_prompt"]
    ext_en = prompts.build_prompts(_state(language="en"))["extension_prompt"]

    assert "최대 개수" in ko
    assert "억지로 채우지 마세요" in ko
    assert "maximum counts" in en
    assert "do not pad" in en
    assert "최대 개수" in ext_ko
    assert "억지로 채우지 마세요" in ext_ko
    assert "maximum counts" in ext_en
    assert "do not pad" in ext_en
