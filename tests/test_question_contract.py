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
