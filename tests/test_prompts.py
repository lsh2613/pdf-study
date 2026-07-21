"""prompts.build_prompts 단위 테스트."""
from __future__ import annotations

import pytest

from pdf_study import prompts


def _state(**overrides):
    base = {
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


def test_prompt_contract_is_korean_only():
    """원문과 무관하게 한국어 학습 자료를 생성하며 언어 필드를 노출하지 않는다."""
    out = prompts.build_prompts(_state())

    assert "language" not in out
    assert "JSON 객체 하나만" in out["summarizer_prompt"]
    assert "한국어 요약" in out["summarizer_prompt"]


def test_extension_prompt_omitted_when_disabled():
    opts = {
        "multiple_choice": True, "short_answer": True,
        "reflection": True, "extension": False,
    }
    out = prompts.build_prompts(_state(question_options=opts))
    assert out["extension_prompt"] is None
    assert out["enabled_types"]["extension"] is False


def test_user_context_and_book_info_are_injected():
    out = prompts.build_prompts(
        _state(user_context="학부생 대상"),
        book_info={
            "title": "테스트 책", "author": "샘플 저자", "publisher": "샘플 출판",
            "preface_summary": "이 책은 데이터베이스 개론서다.",
        },
    )
    for value in ("학부생 대상", "테스트 책", "샘플 저자", "샘플 출판", "데이터베이스 개론서"):
        assert value in out["summarizer_prompt"]
    assert "문제 관점" in out["summarizer_prompt"]
    assert "문제 관점" in out["extension_prompt"]


def test_workflow_instructions_branch_by_execution_mode():
    seq = prompts.build_prompts(_state(execution_mode="sequential"))
    par = prompts.build_prompts(_state(execution_mode="parallel"))
    assert "한 챕터씩 처리하세요" in seq["workflow_instructions"]
    assert "최대 5개 챕터를 동시에" in par["workflow_instructions"]


@pytest.mark.parametrize("execution_mode", ["sequential", "parallel"])
def test_workflow_instructions_use_result_specific_pending_lists(execution_mode):
    workflow = prompts.build_prompts(
        _state(execution_mode=execution_mode)
    )["workflow_instructions"]

    assert "summary_pending_chapter_ids" in workflow
    assert "extension_pending_chapter_ids" in workflow
    assert "요청된 결과 유형만 저장" in workflow


def test_text_mode_input_block_default():
    out = prompts.build_prompts(_state())
    assert out["extraction_mode"] == "text"
    assert "[입력 방식 — 본문 텍스트]" in out["summarizer_prompt"]
    assert "[OCR 모드]" not in out["workflow_instructions"]


def test_ocr_mode_uses_precomputed_text_and_no_image_input():
    out = prompts.build_prompts(_state(extraction_mode="ocr"))
    prompt = out["summarizer_prompt"]
    assert out["extraction_mode"] == "ocr"
    assert "[입력 방식 — OCR 선계산 본문 텍스트]" in prompt
    assert "get_chapter_content가 제공한 text" in prompt
    assert "page_images" not in prompt
    assert "body_text" not in prompt
    assert "비전" not in prompt


def test_chapter_ids_are_naturally_sorted():
    assert prompts.build_prompts(_state())["chapter_ids"] == ["ch1", "ch2", "ch10"]


def test_prompt_chapter_ids_are_pending_union_by_result_type():
    state = _state(chapters={
        "ch1": {"summary_status": "completed", "extension_status": "pending"},
        "ch2": {"summary_status": "pending", "extension_status": "completed"},
        "ch3": {"summary_status": "completed", "extension_status": "completed"},
    })

    out = prompts.build_prompts(state)

    assert out["summary_pending_chapter_ids"] == ["ch2"]
    assert out["extension_pending_chapter_ids"] == ["ch1"]
    assert out["chapter_ids"] == ["ch1", "ch2"]


def test_summary_format_requires_markdown_subchapters_without_images():
    prompt = prompts.build_prompts(_state())["summarizer_prompt"]
    assert "[요약 작성 형식 — 마크다운]" in prompt
    assert "서브 챕터" in prompt
    assert "이미지(그림)는 넣지 마세요" in prompt
    assert "fig:" not in prompt


def test_question_counts_are_quality_limits_not_targets():
    out = prompts.build_prompts(_state())
    assert "최대 개수" in out["summarizer_prompt"]
    assert "억지로 채우지 마세요" in out["summarizer_prompt"]
    assert "최대 개수" in out["extension_prompt"]
    assert "억지로 채우지 마세요" in out["extension_prompt"]


def test_basic_question_guidelines_ground_questions_in_chapter_body():
    prompt = prompts.build_prompts(_state())["summarizer_prompt"]
    assert "[기본 문제 작성 기준]" in prompt
    assert "현재 챕터 본문만으로" in prompt
    assert "그림, 도표, 이미지의 시각 정보에 의존하는 문제는 만들지 마세요" in prompt
    assert "reflection도 기본 문제" in prompt
    assert "본문 근거 제한보다" in prompt


def test_extension_guidelines_use_chapter_and_user_context_without_search():
    prompt = prompts.build_prompts(_state())["extension_prompt"]
    assert "[확장 문제 작성 기준]" in prompt
    assert "단순 회상이나 정의 암기 문제가 아니라" in prompt
    assert "현실 맥락" in prompt
    assert "model_answer는 반드시 포함" in prompt
    assert "외부 검색이나 별도 자료 수집은 하지 않습니다" in prompt
    assert "함께 전달받은 챕터 본문" in prompt
    assert '"sources"' not in prompt
