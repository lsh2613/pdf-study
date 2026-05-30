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


def test_chapter_ids_naturally_sorted():
    out = prompts.build_prompts(_state())
    # ch1, ch2, ch10이 사전순(ch1, ch10, ch2)이 아니라 자연수 순으로
    assert out["chapter_ids"] == ["ch1", "ch2", "ch10"]


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
