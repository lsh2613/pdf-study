from pdf_study import processing_mode_contract


def test_choices_preserve_public_contract_and_return_fresh_copies():
    choices = processing_mode_contract.choices(None)

    assert choices == [
        {
            "execution_mode": "sequential",
            "extraction_mode": "text",
            "label": "Sequential + Text",
            "desc": "디지털 PDF · 안정적·빠르고 저렴",
        },
        {
            "execution_mode": "parallel",
            "extraction_mode": "text",
            "label": "Parallel + Text",
            "desc": "디지털 PDF · 최대 5개 동시로 가장 빠름",
        },
        {
            "execution_mode": "sequential",
            "extraction_mode": "ocr",
            "label": "Sequential + OCR",
            "desc": "스캔본·깨진 PDF · PaddleOCR CPU 선계산 뒤 순차 sub-agent 처리",
        },
        {
            "execution_mode": "parallel",
            "extraction_mode": "ocr",
            "label": "Parallel + OCR",
            "desc": "스캔본·깨진 PDF · PaddleOCR CPU 선계산 뒤 최대 5개 sub-agent 동시 처리",
        },
    ]
    choices[0]["label"] = "mutated"
    assert processing_mode_contract.choices(None)[0]["label"] == "Sequential + Text"


def test_ocr_only_choice_step_and_fallback_data_share_the_same_contract():
    choices = processing_mode_contract.choices("garbled")

    assert choices == processing_mode_contract.set_chapters_next_step("garbled")["choices"]
    assert choices == processing_mode_contract.invalid_mode_data("garbled")["choices"]
    assert processing_mode_contract.invalid_mode_data("garbled") == {
        "choices": choices,
        "user_choice_required": True,
        "user_choice_instruction": (
            "choices의 모든 항목과 설명을 그대로 사용자에게 보여주고, 반드시 사용자에게서 받은 "
            "선택값 중 execution_mode와 extraction_mode만 다음 도구에 전달하세요."
        ),
        "execution_modes": ["sequential", "parallel"],
        "extraction_modes": ["ocr"],
        "text_quality": "garbled",
        "forced_extraction_mode": "ocr",
    }


def test_set_chapters_next_step_requires_presenting_choices_to_the_user():
    step = processing_mode_contract.set_chapters_next_step(None)

    assert step["user_choice_required"] is True
    assert step["user_choice_instruction"] == (
        "choices의 모든 항목과 설명을 그대로 사용자에게 보여주고, 반드시 사용자에게서 받은 "
        "선택값 중 "
        "execution_mode와 extraction_mode만 다음 도구에 전달하세요."
    )


def test_invalid_mode_messages_are_built_from_the_canonical_options():
    standard = processing_mode_contract.invalid_mode_message(None)
    ocr_only = processing_mode_contract.invalid_mode_message("no_text_layer")

    for choice in processing_mode_contract.choices(None):
        assert choice["label"] in standard
    assert "Sequential + Text" not in ocr_only
    for choice in processing_mode_contract.choices("no_text_layer"):
        assert choice["label"] in ocr_only
