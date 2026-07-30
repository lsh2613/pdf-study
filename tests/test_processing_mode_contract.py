from pdf_learner import processing_mode_contract


def test_elicitation_choices_split_extraction_from_execution():
    assert processing_mode_contract.extraction_choices(None) == [
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
    ]
    assert processing_mode_contract.execution_choices() == [
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
    ]


def test_elicitation_extraction_choices_force_ocr():
    assert processing_mode_contract.extraction_choices("garbled") == [
        {
            "value": "ocr",
            "label": "OCR",
            "desc": "PaddleOCR CPU로 본문을 먼저 읽어 텍스트로 저장합니다.",
        },
    ]


def test_set_chapters_next_step_requires_only_agent_generated_chapters():
    assert processing_mode_contract.set_chapters_next_step(None) == {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }


def test_invalid_mode_recovery_does_not_expose_choice_fallback():
    assert processing_mode_contract.invalid_mode_data("garbled") == {
        "text_quality": "garbled",
    }
    assert "내부 처리 모드" in processing_mode_contract.invalid_mode_message(None)
