import copy

from pdf_study import question_contract


def test_summary_contract_reports_existing_missing_paths():
    data = question_contract.summary_payload_example()
    data["questions"]["multiple_choice"][0].pop("options")

    assert question_contract.missing_summary_fields(
        data,
        {"multiple_choice": True, "short_answer": True, "reflection": True},
        "ch1",
    ) == ["questions.multiple_choice[0].options"]


def test_extension_contract_reports_existing_missing_paths():
    data = question_contract.extension_payload_example()
    data["questions"]["extension"][0]["model_answer"] = ""

    assert question_contract.missing_extension_fields(data, "ch1") == [
        "questions.extension[0].model_answer"
    ]


def test_payload_examples_are_fresh_each_time():
    summary = question_contract.summary_payload_example()
    extension = question_contract.extension_payload_example()
    summary["questions"]["short_answer"][0]["question"] = "changed"
    extension["questions"]["extension"].clear()

    assert question_contract.summary_payload_example()["questions"]["short_answer"][0][
        "question"
    ] == "..."
    assert question_contract.extension_payload_example()["questions"]["extension"]


def test_summary_contract_preserves_existing_validation_rules():
    data = question_contract.summary_payload_example()
    data["questions"]["multiple_choice"][0]["answer_index"] = True
    data["questions"]["short_answer"][0]["model_answer"] = " "

    assert question_contract.missing_summary_fields(
        data,
        {"multiple_choice": True, "short_answer": True, "reflection": True},
        "ch1",
    ) == [
        "questions.multiple_choice[0].answer_index",
        "questions.short_answer[0].model_answer",
    ]


def test_extension_contract_requires_nonempty_extension_items():
    data = question_contract.extension_payload_example()
    data["questions"]["extension"] = []

    assert question_contract.missing_extension_fields(data, "ch1") == [
        "questions.extension"
    ]


def test_summary_contract_rejects_question_id_unsafe_for_renderers():
    data = question_contract.summary_payload_example()
    data["questions"]["multiple_choice"][0]["id"] = 'mc"1'

    assert question_contract.missing_summary_fields(
        data,
        {"multiple_choice": True, "short_answer": True, "reflection": True},
        "ch1",
    ) == ["questions.multiple_choice[0].id"]


def test_summary_contract_rejects_duplicate_question_ids_across_types():
    data = question_contract.summary_payload_example()
    data["questions"]["short_answer"][0]["id"] = "mc_1"

    assert question_contract.missing_summary_fields(
        data,
        {"multiple_choice": True, "short_answer": True, "reflection": True},
        "ch1",
    ) == ["questions.short_answer[0].id"]


def test_summary_contract_enforces_short_chapter_question_maximum():
    data = question_contract.summary_payload_example()
    question = data["questions"]["multiple_choice"][0]
    data["questions"]["multiple_choice"] = [
        {**copy.deepcopy(question), "id": f"mc_{index}"}
        for index in range(4)
    ]

    assert question_contract.missing_summary_fields(
        data,
        {"multiple_choice": True, "short_answer": True, "reflection": True},
        "ch1",
        char_count=2_999,
    ) == ["questions.multiple_choice"]


def test_extension_contract_rejects_id_already_saved_for_chapter():
    data = question_contract.extension_payload_example()
    data["questions"]["extension"][0]["id"] = "mc_1"

    assert question_contract.missing_extension_fields(
        data,
        "ch1",
        existing_ids={"mc_1"},
    ) == ["questions.extension[0].id"]


def test_materialize_multiple_choice_places_correct_answer_after_server_shuffle():
    data = question_contract.summary_payload_example()
    item = data["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item.update(
        question="질문",
        explanation="해설",
        correct_answer="정답",
        incorrect_answers=["오답 A", "오답 B"],
    )

    normalized, missing = question_contract.materialize_multiple_choice_options(
        data,
        shuffle_options=lambda values: values.reverse(),
    )

    assert missing == []
    assert normalized["questions"]["multiple_choice"][0] == {
        "id": "mc_1",
        "question": "질문",
        "options": ["오답 B", "오답 A", "정답"],
        "answer_index": 2,
        "explanation": "해설",
    }
    assert "correct_answer" not in normalized["questions"]["multiple_choice"][0]
    assert "correct_answer" in data["questions"]["multiple_choice"][0]


def test_materialize_multiple_choice_rejects_missing_correct_answer():
    data = question_contract.summary_payload_example()
    item = data["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item["incorrect_answers"] = ["오답"]

    _, missing = question_contract.materialize_multiple_choice_options(data)

    assert missing == ["questions.multiple_choice[0].correct_answer"]


def test_materialize_multiple_choice_keeps_processing_later_valid_items():
    data = question_contract.summary_payload_example()
    invalid_item = data["questions"]["multiple_choice"][0]
    invalid_item.pop("options")
    invalid_item.pop("answer_index")
    invalid_item["incorrect_answers"] = ["오답"]
    valid_item = {
        "id": "mc_2",
        "question": "두 번째 질문",
        "correct_answer": "두 번째 정답",
        "incorrect_answers": ["두 번째 오답"],
        "explanation": "두 번째 해설",
    }
    data["questions"]["multiple_choice"].append(valid_item)

    normalized, missing = question_contract.materialize_multiple_choice_options(
        data,
        shuffle_options=lambda values: values.reverse(),
    )

    assert missing == ["questions.multiple_choice[0].correct_answer"]
    assert normalized["questions"]["multiple_choice"][1]["options"] == [
        "두 번째 오답",
        "두 번째 정답",
    ]
