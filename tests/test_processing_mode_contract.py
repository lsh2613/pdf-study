from pdf_learner import processing_mode_contract


def test_elicitation_choices_split_extraction_from_execution():
    assert processing_mode_contract.extraction_choices(None) == [
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
    ]
    assert processing_mode_contract.execution_choices() == [
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
    ]


def test_elicitation_extraction_choices_force_ocr():
    assert processing_mode_contract.extraction_choices("garbled") == [
        {
            "value": "ocr",
            "label": "PyMuPDF + PaddleOCR",
            "desc": "PDF 페이지를 이미지로 렌더링한 뒤 OCR로 본문 추출",
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
