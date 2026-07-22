"""Canonical validation rules and example payloads for question results."""
from __future__ import annotations

import copy
from typing import Any


BASIC_QUESTION_TYPES = ("multiple_choice", "short_answer", "reflection")


def summary_payload_example() -> dict[str, Any]:
    return copy.deepcopy({
        "summary": "요약",
        "key_points": ["핵심 포인트 1", "핵심 포인트 2"],
        "questions": {
            "multiple_choice": [{
                "id": "mc_1",
                "question": "...",
                "options": ["A", "B"],
                "answer_index": 0,
                "explanation": "...",
            }],
            "short_answer": [{
                "id": "sa_1",
                "question": "...",
                "model_answer": "...",
            }],
            "reflection": [{
                "id": "rf_1",
                "question": "...",
                "model_answer": "...",
            }],
        },
    })


def extension_payload_example() -> dict[str, Any]:
    return copy.deepcopy({
        "questions": {
            "extension": [{
                "id": "ex_1",
                "question": "...",
                "model_answer": "...",
            }],
        },
    })


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_string_list(
    value: Any,
    path: str,
    missing: list[str],
    *,
    allow_empty: bool,
) -> bool:
    if not isinstance(value, list):
        missing.append(path)
        return False
    if not allow_empty and not value:
        missing.append(path)
        return False
    valid = True
    for index, item in enumerate(value):
        if not _is_nonempty_str(item):
            missing.append(f"{path}[{index}]")
            valid = False
    return valid


def _validate_required_strings(
    item: dict[str, Any],
    fields: tuple[str, ...],
    path: str,
    missing: list[str],
) -> None:
    for field in fields:
        if not _is_nonempty_str(item.get(field)):
            missing.append(f"{path}.{field}")


def _validate_basic_question_items(
    items: list[Any],
    qtype: str,
    missing: list[str],
) -> None:
    for index, item in enumerate(items):
        path = f"questions.{qtype}[{index}]"
        if not isinstance(item, dict):
            missing.append(path)
            continue

        if qtype == "multiple_choice":
            _validate_required_strings(item, ("id", "question", "explanation"), path, missing)

            options = item.get("options")
            options_ok = _validate_string_list(
                options,
                f"{path}.options",
                missing,
                allow_empty=False,
            )
            if isinstance(options, list) and len(options) < 2:
                missing.append(f"{path}.options")
                options_ok = False

            answer_index = item.get("answer_index")
            if not _is_int(answer_index):
                missing.append(f"{path}.answer_index")
            elif options_ok and not (0 <= answer_index < len(options)):
                missing.append(f"{path}.answer_index")
        else:
            _validate_required_strings(item, ("id", "question", "model_answer"), path, missing)


def missing_summary_fields(
    data: dict[str, Any],
    options: dict[str, bool],
    chapter_id: str,
) -> list[str]:
    """Return missing or invalid required paths in a summary payload."""
    missing: list[str] = []
    if not isinstance(data, dict):
        return ["data"]

    if "chapter_id" in data and data.get("chapter_id") != chapter_id:
        missing.append("chapter_id")
    if "title" in data and not isinstance(data.get("title"), str):
        missing.append("title")

    if not _is_nonempty_str(data.get("summary")):
        missing.append("summary")

    _validate_string_list(data.get("key_points"), "key_points", missing, allow_empty=False)

    questions = data.get("questions")
    if not isinstance(questions, dict):
        missing.append("questions")
        questions = {}

    for qtype in BASIC_QUESTION_TYPES:
        if qtype not in questions or not isinstance(questions.get(qtype), list):
            missing.append(f"questions.{qtype}")
            continue
        items = questions[qtype]
        if options.get(qtype) and not items:
            missing.append(f"questions.{qtype}")
        _validate_basic_question_items(items, qtype, missing)

    return missing


def missing_extension_fields(data: dict[str, Any], chapter_id: str) -> list[str]:
    """Return missing or invalid required paths in an extension payload."""
    missing: list[str] = []
    if not isinstance(data, dict):
        return ["data"]

    if "chapter_id" in data and data.get("chapter_id") != chapter_id:
        missing.append("chapter_id")

    questions = data.get("questions")
    if not isinstance(questions, dict):
        missing.append("questions")
        questions = {}

    extension = questions.get("extension")
    if not isinstance(extension, list) or not extension:
        missing.append("questions.extension")
        return missing

    for index, item in enumerate(extension):
        path = f"questions.extension[{index}]"
        if not isinstance(item, dict):
            missing.append(path)
            continue
        _validate_required_strings(item, ("id", "question", "model_answer"), path, missing)

    return missing
