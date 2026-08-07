"""Canonical validation rules and example payloads for question results."""
from __future__ import annotations

import copy
import random
import re
from typing import Any, Callable

from . import summary_contract


BASIC_QUESTION_TYPES = ("multiple_choice", "short_answer", "reflection")

QUESTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
QUESTION_MAXIMUMS = (
    (3_000, {"multiple_choice": 3, "short_answer": 1, "reflection": 1, "extension": 1}),
    (10_000, {"multiple_choice": 5, "short_answer": 2, "reflection": 2, "extension": 1}),
    (25_000, {"multiple_choice": 7, "short_answer": 3, "reflection": 2, "extension": 2}),
    (None, {"multiple_choice": 10, "short_answer": 4, "reflection": 3, "extension": 3}),
)


class QuestionContractError(ValueError):
    """저장 잠금 안에서 다시 확인한 문제 계약 위반."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"invalid question fields: {missing}")


def summary_payload_example() -> dict[str, Any]:
    result = {
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
    }
    result.update(summary_contract.quality_payload_example())
    return copy.deepcopy(result)


def agent_summary_payload_example() -> dict[str, Any]:
    """생성 agent가 반환할 객관식 정답·오답 분리 예시."""
    example = summary_payload_example()
    # 구조 목록과 검토 결과는 별도 단계에서 생성한 뒤 최종 저장 payload에 합친다.
    example.pop("section_inventory", None)
    example.pop("content_map", None)
    example.pop("summary_review", None)
    multiple_choice = example["questions"]["multiple_choice"][0]
    multiple_choice.pop("options")
    multiple_choice.pop("answer_index")
    multiple_choice.update(
        correct_answer="정답",
        incorrect_answers=["오답 1"],
    )
    return example


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


def _shuffle_choices(choices: list[str]) -> None:
    """운영 저장에서 한 번만 사용할 예측 불가능한 보기 순서 섞기."""
    random.SystemRandom().shuffle(choices)


def materialize_multiple_choice_options(
    data: Any,
    *,
    shuffle_options: Callable[[list[str]], None] | None = None,
) -> tuple[Any, list[str]]:
    """Agent 형식 객관식을 저장·렌더용 정규 형식으로 바꾼다.

    기존 ``options``/``answer_index`` 형식은 그대로 통과시킨다. 새 형식은 정답과
    오답을 합친 뒤 한 번만 섞어 정규 형식으로 바꾼 복사본을 반환한다.
    """
    normalized = copy.deepcopy(data)
    if not isinstance(normalized, dict):
        return normalized, []
    questions = normalized.get("questions")
    if not isinstance(questions, dict):
        return normalized, []
    items = questions.get("multiple_choice")
    if not isinstance(items, list):
        return normalized, []

    missing: list[str] = []
    shuffle = shuffle_options or _shuffle_choices
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if "correct_answer" not in item and "incorrect_answers" not in item:
            continue

        path = f"questions.multiple_choice[{index}]"
        item_missing: list[str] = []
        correct_answer = item.get("correct_answer")
        incorrect_answers = item.get("incorrect_answers")
        if not _is_nonempty_str(correct_answer):
            item_missing.append(f"{path}.correct_answer")
        if (
            not isinstance(incorrect_answers, list)
            or not incorrect_answers
            or any(not _is_nonempty_str(answer) for answer in incorrect_answers)
            or (
                _is_nonempty_str(correct_answer)
                and (
                    correct_answer in incorrect_answers
                    or len(set([correct_answer, *incorrect_answers]))
                    != len(incorrect_answers) + 1
                )
            )
        ):
            item_missing.append(f"{path}.incorrect_answers")
        if item_missing:
            missing.extend(item_missing)
            continue

        choices = [correct_answer, *incorrect_answers]
        shuffle(choices)
        items[index] = {
            "id": item.get("id"),
            "question": item.get("question"),
            "options": choices,
            "answer_index": choices.index(correct_answer),
            "explanation": item.get("explanation"),
        }
    return normalized, missing


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


def question_maximums(char_count: int) -> dict[str, int]:
    """본문 글자 수에 따른 문제 유형별 최대 개수."""
    for upper_bound, limits in QUESTION_MAXIMUMS:
        if upper_bound is None or char_count < upper_bound:
            return dict(limits)
    raise AssertionError("unreachable question maximum")


def question_ids(questions: Any) -> set[str]:
    """저장된 질문 객체에서 비교 가능한 ID만 모은다."""
    if not isinstance(questions, dict):
        return set()
    ids: set[str] = set()
    for items in questions.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and _is_nonempty_str(item.get("id")):
                ids.add(item["id"])
    return ids


def invalid_question_id_paths(
    questions: Any,
    qtypes: tuple[str, ...],
    *,
    existing_ids: set[str] | None = None,
) -> list[str]:
    """문제 ID 형식 또는 현재 챕터에서의 중복 경로를 반환한다."""
    if not isinstance(questions, dict):
        return []
    missing: list[str] = []
    seen = set(existing_ids or ())
    for qtype in qtypes:
        items = questions.get(qtype)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            question_id = item.get("id")
            path = f"questions.{qtype}[{index}].id"
            if not _is_nonempty_str(question_id):
                continue
            if not QUESTION_ID_PATTERN.fullmatch(question_id) or question_id in seen:
                missing.append(path)
            seen.add(question_id)
    return missing


def _validate_question_maximums(
    questions: dict[str, Any],
    qtypes: tuple[str, ...],
    missing: list[str],
    *,
    char_count: int | None,
) -> None:
    if not _is_int(char_count):
        return
    limits = question_maximums(char_count)
    for qtype in qtypes:
        items = questions.get(qtype)
        if isinstance(items, list) and len(items) > limits[qtype]:
            missing.append(f"questions.{qtype}")


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
    *,
    char_count: int | None = None,
    existing_ids: set[str] | None = None,
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

    missing.extend(invalid_question_id_paths(
        questions, BASIC_QUESTION_TYPES, existing_ids=existing_ids,
    ))
    _validate_question_maximums(
        questions, BASIC_QUESTION_TYPES, missing, char_count=char_count,
    )

    return missing


def missing_extension_fields(
    data: dict[str, Any],
    chapter_id: str,
    *,
    char_count: int | None = None,
    existing_ids: set[str] | None = None,
) -> list[str]:
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

    missing.extend(invalid_question_id_paths(
        questions, ("extension",), existing_ids=existing_ids,
    ))
    _validate_question_maximums(
        questions, ("extension",), missing, char_count=char_count,
    )

    return missing
