"""FastMCP 서버 — pdf-learner-builder MCP 도구 등록.

모든 도구는 {ok, error, data, next_action} 형식으로 응답하며,
예외는 raise하지 않고 ok=False로 변환한다 (MCP 통신 안정성).
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model

from . import (
    analysis,
    processing_mode_contract,
    prompts,
    question_contract,
    section_source,
    summary_contract,
    workspace,
)
from .renderer import RENDERERS
from .renderer.output_manager import install_rendered_output
from .renderer.page_labels import format_page_label
from .pdf import chapter as chapter_mod

logger = logging.getLogger(__name__)

MCP_INSTRUCTIONS = """
사용자 선택은 각 도구가 시작하는 MCP form elicitation으로만 받는다. 에이전트가
선택값을 도구 인자로 제공하거나 사용자를 대신해 응답하지 않는다. form elicitation을
지원하지 않는 클라이언트에서는 선택이 필요한 도구가 상태 변경 없이 실패한다.
출력 폴더는 MCP 서버 프로젝트 루트 아래 result/<pdf-name>으로 계산한다.
완료되었거나 진행 중인 결과 경로는 list_study_results로 조회한다.
""".strip()

mcp = FastMCP("pdf-learner-builder", instructions=MCP_INSTRUCTIONS)

SERVER_ROOT = Path(__file__).resolve().parent
RESULT_ROOT = SERVER_ROOT / "result"


_OUTPUT_FORMAT_CHOICES = (
    {
        "value": "html",
        "label": "html",
        "desc": "브라우저에서 학습하고 진도 저장",
    },
    {
        "value": "md_tui",
        "label": "md+tui",
        "desc": "터미널에서 학습하고 진도 저장",
    },
)

_OCR_LANGUAGE_CHOICES = (
    {
        "value": "korean",
        "label": "한국어",
        "desc": "",
    },
    {
        "value": "english",
        "label": "영어",
        "desc": "",
    },
)

_RESUME_CHOICES = (
    {"value": True, "label": "이어가기", "desc": ""},
    {"value": False, "label": "취소", "desc": ""},
)

_CLEANUP_CHOICES = (
    {"value": True, "label": "삭제", "desc": ""},
    {"value": False, "label": "유지", "desc": ""},
)


def _normalize_elicitation_json_schema(schema: dict[str, Any]) -> None:
    """Codex MCP form의 엄격한 최상위 스키마 계약에 맞춘다."""
    schema.pop("title", None)


def _form_choice_value(choice: dict[str, Any]) -> str:
    """설명까지 포함하되 내부 값과 분리된 form 제출값을 만든다."""
    description = str(choice.get("desc") or "").strip()
    value = str(choice["label"])
    return f"{value} — {description}" if description else value


def _form_choice_schema(choices: list[dict[str, Any]]) -> dict[str, Any]:
    """Codex가 지원하는 단순 enum에 설명형 한글 선택값을 직접 넣는다."""
    return {
        "enum": [_form_choice_value(choice) for choice in choices],
    }


def _form_choice_input_type(choices: list[dict[str, Any]]) -> Any:
    """테스트의 기존 내부값도 받되 wire schema는 문자열 enum으로 유지한다."""
    def normalize(value: Any) -> Any:
        for choice in choices:
            internal = choice["value"]
            if type(value) is type(internal) and value == internal:
                return _form_choice_value(choice)
        return value

    return Annotated[str, BeforeValidator(normalize)]


def _resolve_form_choice(
    value: Any,
    choices: list[dict[str, Any]],
    *,
    error: str = "지원하지 않는 선택값입니다.",
) -> Any:
    """form 표시값을 기존 내부 상태값으로 되돌린다."""
    for choice in choices:
        internal = choice["value"]
        if value == _form_choice_value(choice) or (
            type(value) is type(internal) and value == internal
        ):
            return internal
    raise ValueError(error)


class _ElicitationSelection(BaseModel):
    """모든 form Elicitation이 공유하는 Codex 호환 Pydantic 기반."""

    model_config = ConfigDict(
        json_schema_extra=_normalize_elicitation_json_schema,
    )


class _OutputFormatSelection(_ElicitationSelection):
    output_format: str = Field(
        title="최종 학습 자료 형식",
        json_schema_extra=_form_choice_schema(
            [dict(choice) for choice in _OUTPUT_FORMAT_CHOICES],
        ),
    )


class _OcrLanguageSelection(_ElicitationSelection):
    ocr_language: str = Field(
        title="PDF OCR 언어",
        json_schema_extra=_form_choice_schema(
            [dict(choice) for choice in _OCR_LANGUAGE_CHOICES],
        ),
    )


class _ResumeSelection(_ElicitationSelection):
    resume_confirmed: _form_choice_input_type(
        [dict(choice) for choice in _RESUME_CHOICES],
    ) = Field(
        title="기존 작업 이어가기",
        json_schema_extra=_form_choice_schema(
            [dict(choice) for choice in _RESUME_CHOICES],
        ),
    )


class _CleanupSelection(_ElicitationSelection):
    cleanup_confirmed: _form_choice_input_type(
        [dict(choice) for choice in _CLEANUP_CHOICES],
    ) = Field(
        title="중간 작업 데이터 삭제",
        json_schema_extra=_form_choice_schema(
            [dict(choice) for choice in _CLEANUP_CHOICES],
        ),
    )


# ---------------------------------------------------------------------------
# 응답 헬퍼
# ---------------------------------------------------------------------------

def _ok(data: Any = None, next_action: str | None = None) -> dict[str, Any]:
    return {"ok": True, "error": None, "data": data, "next_action": next_action}


def _err(
    error: str,
    data: Any = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    return {"ok": False, "error": error, "data": data, "next_action": next_action}


def _set_chapters_next_step(text_quality: str | None) -> dict[str, Any]:
    return processing_mode_contract.set_chapters_next_step(text_quality)


def _ocr_language_setup() -> dict[str, Any]:
    return {
        "field": "ocr_language",
        "question": "OCR로 읽을 PDF의 언어를 선택하세요.",
        "choices": [dict(choice) for choice in _OCR_LANGUAGE_CHOICES],
    }


def _prepare_ocr_next_step() -> dict[str, Any]:
    return {
        "tool": "prepare_ocr",
        "required_parameters": [],
    }


def _finalize_next_step() -> dict[str, Any]:
    return {
        "tool": "finalize_study",
        "required_parameters": [],
    }


_CHOICE_FALLBACK_KEYS = frozenset({
    "choices",
    "user_choice_required",
    "user_choice_instruction",
    "user_choice_options",
    "user_choices",
    "question_setup",
    "ocr_language_setup",
})


def _without_choice_fallback(value: Any) -> Any:
    """MCP 공개 응답에서 Elicitation을 우회할 구조화 선택 데이터를 제거한다."""
    if isinstance(value, dict):
        return {
            key: _without_choice_fallback(nested)
            for key, nested in value.items()
            if key not in _CHOICE_FALLBACK_KEYS
        }
    if isinstance(value, list):
        return [_without_choice_fallback(nested) for nested in value]
    return value


def _save_target_error(exc: BaseException, chapter_id: str) -> dict[str, Any]:
    message = str(exc)
    if "unknown work_id" in message:
        missing = ["work_id"]
    elif isinstance(exc, (RuntimeError, OSError)):
        missing = ["state"]
    else:
        missing = ["chapter_id"]
    return _err(
        f"{type(exc).__name__}: {message}",
        data={"missing": missing, "chapter_id": chapter_id},
    )


def _ensure_save_target(state: dict[str, Any], chapter_id: str) -> None:
    entry = state.get("chapters", {}).get(chapter_id)
    if entry is None:
        raise KeyError(f"chapter not in state: {chapter_id}")
    if entry.get("skip"):
        raise ValueError(f"chapter is skipped: {chapter_id}")


def _chapter_summary_basis(
    work_id: str,
    chapter_id: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """문제 생성에 노출할 저장 요약만 검증해 반환한다."""
    state = state or workspace.load_state(work_id)
    _ensure_save_target(state, chapter_id)
    entry = state["chapters"][chapter_id]
    if entry.get("summary_status") != "completed":
        raise RuntimeError(f"chapter summary is not completed: {chapter_id}")

    saved = workspace.get_chapter_summary(work_id, chapter_id)
    summary = saved.get("summary")
    key_points = saved.get("key_points")
    source_char_count = entry.get("char_count")
    if not isinstance(summary, str) or not summary.strip():
        raise RuntimeError(f"chapter summary is blank: {chapter_id}")
    if (
        not isinstance(key_points, list)
        or not key_points
        or any(not isinstance(item, str) or not item.strip() for item in key_points)
    ):
        raise RuntimeError(f"chapter key_points are invalid: {chapter_id}")
    if type(source_char_count) is not int:
        raise RuntimeError(f"chapter char_count is invalid: {chapter_id}")

    result = {
        "chapter_id": chapter_id,
        "summary": summary,
        "key_points": key_points,
        "source_char_count": source_char_count,
    }
    if isinstance(saved.get("title"), str):
        result["title"] = saved["title"]
    elif isinstance(entry.get("title"), str):
        result["title"] = entry["title"]
    return result


def _invalid_extension_summary_bases(
    work_id: str,
    state: dict[str, Any],
    chapter_ids: set[str],
) -> list[dict[str, str]]:
    invalid: list[dict[str, str]] = []
    for chapter_id in sorted(chapter_ids):
        try:
            _chapter_summary_basis(work_id, chapter_id, state)
        except (KeyError, FileNotFoundError, RuntimeError, ValueError) as exc:
            invalid.append({"chapter_id": chapter_id, "error": str(exc)})
    return invalid


def _pending_kinds(state: dict[str, Any], chapter_id: str) -> list[str]:
    pending = workspace.pending_chapters_from_state(state)
    kinds: list[str] = []
    if chapter_id in pending["summary_pending"]:
        kinds.append("summary")
    if chapter_id in pending["extension_pending"]:
        kinds.append("extension")
    return kinds


def _ready_to_finalize(state: dict[str, Any]) -> bool:
    pending = workspace.pending_chapters_from_state(state)
    return (
        state.get("phases", {}).get("chapter_setup") == "completed"
        and not pending["summary_pending"]
        and not pending["extension_pending"]
    )


def _omitted_chapters(state: dict[str, Any]) -> list[dict[str, Any]]:
    """최종 자료에 들어가지 못한 챕터 결과를 상태 스냅샷에서 설명한다."""
    pending = workspace.pending_chapters_from_state(state)
    pending_summary = set(pending["summary_pending"])
    pending_extension = set(pending["extension_pending"])
    omitted: list[dict[str, Any]] = []
    for chapter_id in sorted(pending_summary | pending_extension):
        entry = state["chapters"][chapter_id]
        results: list[dict[str, Any]] = []
        if chapter_id in pending_summary:
            results.append({
                "type": "summary",
                "status": entry.get("summary_status"),
                "error": entry.get("error"),
            })
        if chapter_id in pending_extension:
            results.append({
                "type": "extension",
                "status": entry.get("extension_status"),
                "error": entry.get("error"),
            })
        omitted.append({"chapter_id": chapter_id, "results": results})
    return omitted


def _omitted_chapters_notice(omitted: list[dict[str, Any]]) -> str:
    if not omitted:
        return ""
    lines = ["\n\n[미반영 챕터 결과] 아래 결과는 최종 자료에 포함되지 않았습니다."]
    for chapter in omitted:
        results = []
        for result in chapter["results"]:
            detail = f"{result['type']}={result['status']}"
            if result["error"]:
                detail += f" ({result['error']})"
            results.append(detail)
        lines.append(f"- {chapter['chapter_id']}: " + ", ".join(results))
    return "\n".join(lines)


def _pending_guidance(
    state: dict[str, Any],
    work_id: str,
    chapter_id: str | None = None,
) -> str:
    """현재 state 스냅샷에서 실제로 남은 결과 종류만 안내한다."""
    if chapter_id is not None:
        kinds = _pending_kinds(state, chapter_id)
        actions: list[str] = []
        if "summary" in kinds:
            actions.append(
                "section_inventory_prompt → 조건부 section_review_prompt → "
                "get_section_content → summary_prompt → "
                "review_prompt → basic_question_prompt 순서로 요약을 먼저 확정하고, "
                "문제 단계에는 "
                "요약·핵심 포인트만 전달한 뒤 "
                f'save_chapter_result(work_id="{work_id}", '
                f'chapter_id="{chapter_id}", data=...)로 저장하세요'
            )
        if "extension" in kinds:
            actions.append(
                "요약 저장 후 get_chapter_summary로 요약만 받아 extension_prompt로 "
                "확장 문제를 만들어 "
                f'save_extension_result(work_id="{work_id}", '
                f'chapter_id="{chapter_id}", data=...)로 저장하세요'
            )
        if actions:
            return (
                f"이 챕터({chapter_id})에서 "
                + ". ".join(actions)
                + f'. 완료 후 list_pending_chapters(work_id="{work_id}")로 '
                "전체 누락을 확인하세요."
            )
        return (
            f"{chapter_id}에 남은 결과가 없습니다. "
            f'list_pending_chapters(work_id="{work_id}")로 전체 누락을 확인하고, '
            "모두 완료됐으면 finalize_study로 진행하세요."
        )

    pending = workspace.pending_chapters_from_state(state)
    summary_pending = pending["summary_pending"]
    extension_pending = pending["extension_pending"]
    if not summary_pending and not extension_pending and _ready_to_finalize(state):
        return (
            f"summary_pending={summary_pending}, extension_pending={extension_pending}. "
            f'남은 챕터가 없습니다. finalize_study(work_id="{work_id}")를 '
            "호출하면 서버가 최종 결과 형식 Elicitation을 엽니다."
        )

    if not summary_pending and not extension_pending:
        return (
            "챕터가 아직 확정되지 않았습니다. scan_pdf 결과 또는 목차 OCR 결과를 확인해 "
            "챕터와 처리 모드를 구성한 뒤 set_chapters를 호출하세요."
        )

    instructions: list[str] = []
    if summary_pending:
        instructions.append(
            f"summary_pending={summary_pending}는 section_inventory_prompt → "
            "조건부 section_review_prompt → get_section_content → summary_prompt → "
            "review_prompt로 요약을 확정한 뒤 "
            "basic_question_prompt에는 요약만 전달하고 save_chapter_result로 "
            "함께 저장하세요"
        )
    else:
        instructions.append(f"summary_pending={summary_pending}")
    if extension_pending:
        instructions.append(
            f"extension_pending={extension_pending}는 get_chapter_summary의 요약만 "
            "extension_prompt에 전달해 save_extension_result로 저장하세요"
        )
    else:
        instructions.append(f"extension_pending={extension_pending}")
    return (
        f'get_subagent_prompts(work_id="{work_id}")로 워크플로를 받은 뒤 '
        + ". ".join(instructions)
        + ". 모두 끝나면 list_pending_chapters로 확인하세요."
    )


def _resume_response(state: dict[str, Any]) -> dict[str, Any]:
    """이미 복원한 상태를 공개 resume 응답으로 변환한다."""
    work_id = state["work_id"]
    question_setup = _question_setup_payload(state)
    if question_setup["pending_fields"] or (
        state.get("page_count") is None
        and question_setup["user_context_request"]
    ):
        return _without_choice_fallback(_ok(
            {
                "work_id": work_id,
                "output_dir": state.get("output_dir"),
                "current_phase": state.get("current_phase"),
                "question_options": state.get("question_options"),
                "summary_pending": [],
                "extension_pending": [],
            },
            next_action=_question_setup_next_action(work_id, question_setup),
        ))

    pending = workspace.pending_chapters_from_state(state)
    data = {
        "work_id": work_id,
        "output_dir": state.get("output_dir"),
        "current_phase": state.get("current_phase"),
        "execution_mode": state.get("execution_mode"),
        "extraction_mode": state.get("extraction_mode"),
        "summary_pending": pending["summary_pending"],
        "extension_pending": pending["extension_pending"],
    }
    if _ready_to_finalize(state):
        data["next_step"] = _finalize_next_step()
    return _without_choice_fallback(_ok(
        data,
        next_action=_pending_guidance(state, work_id),
    ))


def _failed_chapters_from_invalid(invalid: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for item in invalid:
        codes = {reason.get("code") for reason in item.get("reasons", [])}
        if "ocr_failed" not in codes:
            continue
        failed.append({
            "chapter_id": item.get("chapter_id"),
            "failed_pages": item.get("failed_pages", []),
            "error": item.get("error") or "OCR failed",
        })
    return failed


_SAFE_NAME_RE = re.compile(r"[^\w가-힣.\-]+")  # 영숫자 / 한글 / _ . - 외엔 치환


_QUESTION_SETUP_DEFS = (
    {
        "field": "enable_short_answer",
        "state_key": "short_answer",
        "title": "단답형 문제 생성",
        "question": (
            "학습자의 핵심 개념 이해를 빠르게 확인하기 위한 단답형 문제를 "
            "포함할까요?"
        ),
        "choices": [
            {
                "value": True,
                "label": "단답형 문제 포함",
                "desc": "",
            },
            {
                "value": False,
                "label": "단답형 문제 제외",
                "desc": "",
            },
        ],
    },
    {
        "field": "enable_reflection",
        "state_key": "reflection",
        "title": "주관식 문제 생성",
        "question": (
            "학습자가 핵심 내용을 자신의 말로 설명할 수 있는지 확인하는 주관식 "
            "문제를 포함할까요?"
        ),
        "choices": [
            {
                "value": True,
                "label": "주관식 문제 포함",
                "desc": "",
            },
            {
                "value": False,
                "label": "주관식 문제 제외",
                "desc": "",
            },
        ],
    },
    {
        "field": "enable_extension",
        "state_key": "extension",
        "title": "확장 문제 생성",
        "question": (
            "확장 문제는 PDF 개념을 학습자의 현실·실무 맥락과 연결하는 응용 "
            "문제를 의미합니다."
        ),
        "choices": [
            {
                "value": True,
                "label": "확장 문제 포함",
                "desc": "",
            },
            {
                "value": False,
                "label": "확장 문제 제외",
                "desc": "",
            },
        ],
    },
)


def _question_setup_payload(state: dict[str, Any]) -> dict[str, Any]:
    """아직 답하지 않은 문제 유형과 선택적 학습자 정보 요청을 반환한다."""
    options = state.get("question_options") or {}
    questions = [
        {
            "field": item["field"],
            "title": item["title"],
            "question": item["question"],
            "choices": [dict(choice) for choice in item["choices"]],
        }
        for item in _QUESTION_SETUP_DEFS
        if options.get(item["state_key"]) is None
    ]
    setup = {
        "pending_fields": [item["field"] for item in questions],
        "questions": questions,
        "has_enabled_optional_questions": any(
            options.get(item["state_key"]) is True
            for item in _QUESTION_SETUP_DEFS
        ),
        "user_context_request": (
            None
            if state.get("user_context_confirmed") or state.get("user_context")
            else {
                "field": "user_context",
                "required": False,
                "label": "학습자 정보 (선택)",
                "desc": (
                    "학습 목적, 배경지식, 관심 분야, 현재 수준을 알려주면 문제의 "
                    "난이도·표현·예시·관점을 학습자에게 맞출 수 있습니다. "
                    "제공하지 않아도 진행할 수 있습니다."
                ),
            }
        ),
    }
    return setup


def _question_setup_next_action(work_id: str, setup: dict[str, Any]) -> str:
    return (
        f'scan_pdf(work_id="{work_id}")를 호출하면 서버가 미정 문제 유형을 묻고, '
        "선택형 문제가 있으면 학습자 정보 Elicitation을 이어서 엽니다."
    )


def _pdf_name_slug(pdf_path: str) -> str:
    """PDF 파일명을 디렉토리 이름으로 안전하게 정규화."""
    stem = Path(pdf_path).stem
    safe = _SAFE_NAME_RE.sub("_", stem)
    safe = re.sub(r"_+", "_", safe).strip("_.")
    return safe or "study"


def _client_supports(ctx: Context, capability: types.ClientCapabilities) -> bool:
    checker = getattr(getattr(ctx, "session", None), "check_client_capability", None)
    if not callable(checker):
        return False
    try:
        return bool(checker(capability))
    except (AttributeError, TypeError, ValueError):
        return False


def _client_supports_elicitation(ctx: Context) -> bool:
    return _client_supports(
        ctx,
        types.ClientCapabilities(elicitation=types.ElicitationCapability()),
    )


def _elicitation_required(ctx: Context) -> dict[str, Any] | None:
    if _client_supports_elicitation(ctx):
        return None
    return _err(
        "이 도구는 사용자 선택을 직접 받기 위해 MCP form elicitation 지원이 필요합니다.",
        data={"required_capability": "elicitation.form"},
    )


def _resolve_output_dir(pdf_path: str) -> str:
    """MCP 서버 프로젝트의 고정 result 루트 아래 출력 폴더를 계산한다."""
    return str((RESULT_ROOT / _pdf_name_slug(pdf_path)).resolve())


def _elicitation_cancelled(data: dict[str, Any]) -> dict[str, Any]:
    return _err(
        "필수 Elicitation이 승인 응답 없이 종료되어 다음 단계를 실행하지 않았습니다.",
        data=_without_choice_fallback(data),
        next_action=(
            "폼이 표시되지 않았다면 클라이언트의 approval_policy에서 MCP "
            "Elicitation이 허용되는지 확인하고 새 세션에서 같은 도구를 다시 호출하세요."
        ),
    )


async def _elicit_question_setup(
    ctx: Context,
    setup: dict[str, Any],
    *,
    require_user_context: bool = False,
) -> dict[str, Any] | None:
    selected: dict[str, Any] = {}
    # Codex 클라이언트가 다중 필드 form의 탐색 순서를 재배열할 수 있으므로 문제
    # 유형은 단일 필드 form으로 하나씩 열어 단답형 → 주관식 → 확장형을 보장한다.
    for question in setup["questions"]:
        field = question["field"]
        schema = create_model(
            f"PdfLearnerQuestionSetupSelection_{field}",
            __base__=_ElicitationSelection,
            **{
                field: (
                    _form_choice_input_type(question["choices"]),
                    Field(
                        title=question["title"],
                        json_schema_extra=_form_choice_schema(
                            question["choices"],
                        ),
                    ),
                ),
            },
        )
        result = await ctx.elicit(message=question["question"], schema=schema)
        if result.action != "accept" or result.data is None:
            return None
        if not hasattr(result.data, field):
            raise ValueError(f"문제 유형 응답에 {field} 값이 없습니다.")
        raw_value = getattr(result.data, field)
        selected[field] = _resolve_form_choice(
            raw_value,
            question["choices"],
            error="지원하지 않는 문제 유형 선택값입니다.",
        )

    context_is_useful = (
        require_user_context
        or setup["has_enabled_optional_questions"]
        or any(
            selected.get(question["field"]) is True
            for question in setup["questions"]
        )
    )
    if setup["user_context_request"] and context_is_useful:
        if require_user_context:
            context_field = Field(
                title="학습자 정보",
                description=(
                    "학습 목적, 배경지식, 관심 분야, 현재 수준 등을 입력해주세요."
                ),
                min_length=1,
            )
        else:
            context_field = Field(
                default="",
                title="학습자 정보 (선택)",
                description="학습 목적, 배경지식, 관심 분야, 현재 수준 등",
            )
        context_schema = create_model(
            "PdfLearnerUserContextSelection",
            __base__=_ElicitationSelection,
            user_context=(
                str,
                context_field,
            ),
        )
        context_message = (
            "학습자에 최적화된 문제를 만들기 위해 학습자 정보를 제공해주세요."
        )
        context_result = await ctx.elicit(
            message=context_message,
            schema=context_schema,
        )
        if context_result.action != "accept" or context_result.data is None:
            return None
        selected["user_context"] = context_result.data.user_context
    elif setup["user_context_request"]:
        # 선택형 문제가 모두 꺼져 있으면 학습자 정보는 묻지 않고 확정된 빈 값으로 둔다.
        selected["user_context"] = ""
    return selected


async def _elicit_chapter_setup(
    ctx: Context,
    work_id: str,
    chapters: list[dict[str, Any]],
) -> dict[str, Any] | None:
    outline = workspace.load_outline(work_id) or {}
    recommendations = outline.get("recommendations") or {}
    chapter_choices = recommendations.get("user_choice_options") or []
    if not chapter_choices:
        chapter_choices = [{
            "value": "proceed",
            "label": "이대로 진행",
            "desc": "현재 챕터 구성과 범위 사용",
        }]
    schema = create_model(
        "PdfLearnerChapterSetupSelection",
        __base__=_ElicitationSelection,
        chapter_strategy=(
            str,
            Field(
                title="챕터 구성 방식",
                json_schema_extra=_form_choice_schema(chapter_choices),
            ),
        ),
    )
    chapter_lines = []
    page_offset = recommendations.get("page_offset")
    for chapter in chapters:
        page_label = format_page_label(chapter, page_offset=page_offset)
        title = _chapter_display_title(chapter)
        chapter_lines.append(
            f"- {chapter.get('chapter_id')}: {title} / {page_label}"
        )
    message = (
        "[pdf-learner가 분석한 챕터]\n"
        + "\n".join(chapter_lines)
    )
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    selected = result.data.model_dump()
    selected["chapter_strategy"] = _resolve_form_choice(
        selected["chapter_strategy"],
        chapter_choices,
        error="지원하지 않는 챕터 구성 방식입니다.",
    )
    return selected


def _chapter_display_title(chapter: dict[str, Any]) -> str:
    """Form 제목 끝의 중복 페이지 메타만 제거한다."""
    title = str(chapter.get("title") or "").strip()
    normalized = workspace.canonicalize_chapter_page_metadata(chapter)
    pdf_pages = normalized.get("pdf_pages")
    if not isinstance(pdf_pages, (list, tuple)) or len(pdf_pages) != 2:
        return title

    separator = r"\s*[-–—~]\s*"
    pdf_range = (
        rf"{re.escape(str(pdf_pages[0]))}{separator}"
        rf"{re.escape(str(pdf_pages[1]))}"
    )
    pdf_label = rf"PDF\s*p\.\s*{pdf_range}"

    source_present = "source_pages" in normalized
    source_pages = normalized.get("source_pages")
    source_label = ""
    if isinstance(source_pages, (list, tuple)) and len(source_pages) == 2:
        source_range = (
            rf"{re.escape(str(source_pages[0]))}{separator}"
            rf"{re.escape(str(source_pages[1]))}"
        )
        source_label = rf"원문\s*p\.\s*{source_range}"
    elif source_present and source_pages is None:
        source_label = r"원문\s*페이지\s*(?:미상|없음)"

    source_suffix = (
        rf"(?:\s*[,\u00b7/]\s*{source_label}|\s*\(\s*{source_label}\s*\))?"
        if source_label
        else ""
    )
    suffix = (
        rf"\s*\(\s*{pdf_label}{source_suffix}\s*\)"
        rf"\s*$"
    )
    cleaned = re.sub(suffix, "", title, flags=re.IGNORECASE).rstrip()
    return cleaned or title


def _source_pages_for_pdf_range(
    pdf_pages: list[int],
    page_offset: int | None,
) -> list[int] | None:
    if page_offset is None:
        return None
    start, end = pdf_pages
    source_start, source_end = start - page_offset, end - page_offset
    if source_end < 1:
        return None
    return [max(1, source_start), source_end]


def _parse_manual_chapters(
    value: str,
    *,
    page_basis: str,
    page_offset: int | None,
) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        title, separator, raw_range = line.partition("|")
        match = re.fullmatch(r"\s*(\d+)\s*[-–~]\s*(\d+)\s*", raw_range)
        if not separator or not title.strip() or match is None:
            raise ValueError(
                f"직접 입력 {line_number}행은 '제목 | 시작-끝' 형식이어야 합니다."
            )
        start, end = int(match.group(1)), int(match.group(2))
        if start < 1 or end < start:
            raise ValueError(
                f"직접 입력 {line_number}행의 페이지 범위가 올바르지 않습니다."
            )
        if page_basis == "source":
            if page_offset is None:
                raise ValueError(
                    "원문 페이지 번호를 PDF 페이지로 바꿀 오프셋이 확인되지 않았습니다."
                )
            pdf_pages = [start + page_offset, end + page_offset]
            source_pages: list[int] | None = [start, end]
        else:
            pdf_pages = [start, end]
            source_pages = _source_pages_for_pdf_range(pdf_pages, page_offset)
        chapters.append({
            "chapter_id": f"ch{len(chapters) + 1}",
            "title": title.strip(),
            "pdf_pages": pdf_pages,
            "source_pages": source_pages,
        })
    if not chapters:
        raise ValueError("직접 입력한 챕터가 없습니다.")
    return chapters


async def _elicit_manual_chapters(
    ctx: Context,
    work_id: str,
) -> list[dict[str, Any]] | None:
    outline = workspace.load_outline(work_id) or {}
    recommendations = outline.get("recommendations") or {}
    page_offset = recommendations.get("page_offset")
    page_basis_choices = [
        {"value": "pdf", "label": "PDF 페이지 번호", "desc": ""},
    ]
    if page_offset is not None:
        page_basis_choices.append(
            {"value": "source", "label": "원문 페이지 번호", "desc": ""},
        )
    schema = create_model(
        "PdfLearnerManualChapterSelection",
        __base__=_ElicitationSelection,
        manual_chapters=(
            str,
            Field(
                title="챕터 제목과 페이지 범위",
                description="한 줄에 하나씩 '제목 | 시작-끝' 형식으로 입력",
            ),
        ),
        manual_page_basis=(
            str,
            Field(
                title="입력할 페이지 번호 기준",
                json_schema_extra=_form_choice_schema(page_basis_choices),
            ),
        ),
    )
    message = (
        "직접 구성할 챕터를 한 줄에 하나씩 입력해주세요.\n"
        "예: 01. 소개 | 20-23"
    )
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    page_basis = str(_resolve_form_choice(
        result.data.manual_page_basis,
        page_basis_choices,
        error="지원하지 않는 페이지 번호 기준입니다.",
    ))
    return _parse_manual_chapters(
        result.data.manual_chapters,
        page_basis=page_basis,
        page_offset=page_offset,
    )


async def _elicit_chunk_size(
    ctx: Context,
    work_id: str,
) -> list[dict[str, Any]] | None:
    state = workspace.load_state(work_id)
    page_count = int(state.get("page_count") or 0)
    default_chunk_size = max(1, min(chapter_mod.DEFAULT_CHUNK_SIZE, page_count))
    schema = create_model(
        "PdfLearnerChunkSizeSelection",
        __base__=_ElicitationSelection,
        chunk_size=(
            int,
            Field(
                title="청크당 PDF 페이지 수",
                description="PDF를 몇 페이지씩 나눌지 입력",
                ge=1,
                le=page_count,
                default=default_chunk_size,
            ),
        ),
    )
    message = (
        f"기본 분할 크기는 청크당 {default_chunk_size}페이지입니다. "
        "필요하면 변경해주세요."
    )
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    chapters = chapter_mod.make_chunks(page_count, result.data.chunk_size)
    outline = workspace.load_outline(work_id) or {}
    page_offset = (outline.get("recommendations") or {}).get("page_offset")
    for chapter in chapters:
        chapter["source_pages"] = _source_pages_for_pdf_range(
            chapter["pdf_pages"],
            page_offset,
        )
    return chapters


async def _elicit_extraction_mode(ctx: Context, work_id: str) -> str | None:
    text_quality = workspace.load_state(work_id).get("text_quality")
    choices = processing_mode_contract.extraction_choices(text_quality)
    schema = create_model(
        "PdfLearnerExtractionModeSelection",
        __base__=_ElicitationSelection,
        extraction_mode=(
            str,
            Field(
                title="본문 추출 방식",
                json_schema_extra=_form_choice_schema(choices),
            ),
        ),
    )
    if text_quality == "garbled":
        message = "PDF 텍스트 인코딩이 깨져 있어 OCR 방식만 사용할 수 있습니다."
    elif text_quality == "no_text_layer":
        message = "PDF에 사용할 수 있는 텍스트 레이어가 없어 OCR 방식만 사용할 수 있습니다."
    else:
        message = "PDF 본문을 추출할 방식을 선택해주세요."
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    return str(_resolve_form_choice(
        result.data.extraction_mode,
        choices,
        error="지원하지 않는 본문 추출 방식입니다.",
    ))


async def _elicit_execution_mode(ctx: Context) -> str | None:
    choices = processing_mode_contract.execution_choices()
    schema = create_model(
        "PdfLearnerExecutionModeSelection",
        __base__=_ElicitationSelection,
        execution_mode=(
            str,
            Field(
                title="챕터 실행 방식",
                json_schema_extra=_form_choice_schema(choices),
            ),
        ),
    )
    message = "챕터를 처리할 방식을 선택해주세요."
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    return str(_resolve_form_choice(
        result.data.execution_mode,
        choices,
        error="지원하지 않는 챕터 실행 방식입니다.",
    ))


def _safe(label: str):
    """MCP 도구의 예외를 ok=False 응답으로 변환 (sync/async 모두 지원)."""
    def deco(fn):
        import asyncio
        from functools import wraps

        def _handle(e: BaseException):
            if isinstance(e, (KeyError, ValueError, FileNotFoundError, RuntimeError)):
                logger.warning("%s failed: %s", label, e)
                return _err(f"{type(e).__name__}: {e}")
            if isinstance(e, NotImplementedError):
                return _err(f"NotImplemented: {e}")
            logger.exception("%s unexpected error", label)
            return _err(f"unexpected {type(e).__name__}: {e}")

        if asyncio.iscoroutinefunction(fn):
            @wraps(fn)
            async def aw(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    return _handle(e)
            return aw

        @wraps(fn)
        def sw(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                return _handle(e)
        return sw
    return deco


# ---------------------------------------------------------------------------
# 1. init_work
# ---------------------------------------------------------------------------

@mcp.tool(name="init_work")
@_safe("init_work")
async def init_work(
    pdf_path: str,
    ctx: Context,
) -> dict[str, Any]:
    """워크스페이스를 생성하고 work_id를 발급합니다.

    이 MCP는 로컬 PDF를 학습 자료로 변환하기 위한 도구입니다. 사용자가 PDF 경로와
    함께 "학습", "요약본", "검증 문제", "퀴즈", "HTML", "TUI", "study material",
    "summary for learning" 같은 의도를 말하면 일반 PDF 요약으로 처리하지 말고
    반드시 이 MCP 워크플로를 사용하세요.

    기본 흐름:
    init_work → scan_pdf → set_chapters → get_subagent_prompts →
    get_chapter_content → get_section_content → save_chapter_result
    (+ extension이면 save_extension_result) →
    list_pending_chapters → finalize_study

    목차 OCR 분기:
    scan_pdf에서 내장 목차가 없거나 force_vision=True로 재분석이 필요하면
    scan_pdf → prepare_ocr → scan_toc_with_ocr → set_chapters 순서로 진행하세요.
    scan_pdf는 OCR 모델 다운로드/로드/실행을 하지 않고 목차 후보 이미지만
    준비합니다.

    단순 요약만 요청하더라도 "학습하기 위해", "개발자가 공부하기 위해",
    "학습 자료로"처럼 학습 목적이 드러나면 이 MCP의 요약/문제/HTML 또는 TUI
    생성 흐름을 우선 사용하세요.

    Do not directly summarize a PDF when the request is to create learning
    material from a PDF. Use this MCP workflow instead.

    공개 MCP 입력은 pdf_path 하나뿐입니다. 출력 폴더는 MCP 서버 프로젝트 루트의
    `result/<pdf_basename>/`으로 고정하며 요청 workspace나 프로세스 cwd에 따라
    달라지지 않습니다. 문제 유형과 필수 학습자 정보는 서버가 form Elicitation으로
    직접 받습니다. 기존 관리 작업이 있으면 재개/교체도 같은 호출 안의
    Elicitation으로 확인합니다. Elicitation을 지원하지 않으면 상태를 바꾸지 않고
    실패합니다.

    처리 모드(순차/병렬 · text/OCR)는 set_chapters 호출 중 별도 Elicitation으로
    확정합니다.
    다음 단계: scan_pdf(work_id)
    """
    capability_error = _elicitation_required(ctx)
    if capability_error is not None:
        return capability_error
    resolved_dir = _resolve_output_dir(pdf_path)

    existing = workspace.inspect_output_dir(resolved_dir)
    replace_existing = False
    if existing["kind"] == "unmanaged_content":
        return _err(
            "고정 출력 폴더에 pdf-learner가 관리하지 않는 파일이 있어 사용할 수 없습니다.",
            data={"output_dir": resolved_dir, "existing_work": existing},
        )
    if existing["kind"] != "available":
        allowed_actions = (
            ["resume", "replace"] if existing["can_resume"] else ["replace"]
        )
        action_choices = [
            {
                "value": "resume",
                "label": "이어가기",
                "desc": "기존 상태에서 남은 작업을 계속",
            },
            {
                "value": "replace",
                "label": "교체",
                "desc": "같은 결과 폴더에서 새 작업 시작",
            },
        ]
        action_choices = [
            choice for choice in action_choices
            if choice["value"] in allowed_actions
        ]
        action_schema = create_model(
            "PdfLearnerExistingWorkActionSelection",
            __base__=_ElicitationSelection,
            action=(
                str,
                Field(
                    title="기존 작업 처리",
                    json_schema_extra=_form_choice_schema(action_choices),
                ),
            ),
        )
        action_result = await ctx.elicit(
            message="고정 출력 폴더에 기존 pdf-learner 작업이 있습니다.",
            schema=action_schema,
        )
        if action_result.action != "accept" or action_result.data is None:
            return _elicitation_cancelled({
                "output_dir": resolved_dir,
                "existing_work": existing,
            })
        action = str(_resolve_form_choice(
            action_result.data.action,
            action_choices,
            error="지원하지 않는 기존 작업 처리 방식입니다.",
        ))
        if action == "resume":
            state = workspace.resume_workspace(resolved_dir)
            return _resume_response(state)
        replace_existing = True

    initial_setup = _question_setup_payload({
        "question_options": {
            "short_answer": None,
            "reflection": None,
            "extension": None,
        },
        "user_context": "",
        "user_context_confirmed": False,
    })
    selected = await _elicit_question_setup(
        ctx,
        initial_setup,
        require_user_context=True,
    )
    if selected is None:
        return _elicitation_cancelled({"output_dir": resolved_dir})
    user_context = (selected.pop("user_context", None) or "").strip()
    if not user_context:
        return _err(
            "학습자 정보는 비워둘 수 없습니다.",
            data={"output_dir": resolved_dir},
            next_action="학습자 정보를 입력해 같은 init_work를 다시 호출하세요.",
        )
    options = {
        "multiple_choice": True,
        "short_answer": selected["enable_short_answer"],
        "reflection": selected["enable_reflection"],
        "extension": selected["enable_extension"],
    }
    workspace.validate_workspace_inputs(pdf_path, options, user_context)

    existing = workspace.inspect_output_dir(resolved_dir)
    if existing["kind"] != "available":
        if replace_existing and existing["kind"] in {
            "managed_work", "damaged_managed_work", "managed_output",
        }:
            workspace.replace_workspace(resolved_dir)
        else:
            return _err(
                "출력 폴더가 비어 있지 않아 기존 파일을 자동으로 덮어쓰지 않았습니다.",
                data={
                    "output_dir": existing["output_dir"],
                    "existing_work": existing,
                },
                next_action=None,
            )

    work_id = workspace.create_workspace(
        pdf_path=pdf_path,
        output_dir=resolved_dir,
        options=options,
        user_context=user_context,
        user_context_confirmed=True,
    )
    state = workspace.load_state(work_id)
    return _without_choice_fallback(_ok(
        {
            "work_id": work_id,
            "work_dir": str(workspace.get_work_dir(work_id)),
            "output_dir": resolved_dir,
            "question_options": state["question_options"],
        },
        next_action=f'scan_pdf(work_id="{work_id}", scan_size=30)',
    ))


# ---------------------------------------------------------------------------
# 1b. resume_work
# ---------------------------------------------------------------------------

@mcp.tool(name="resume_work")
@_safe("resume_work")
async def resume_work(
    pdf_path: str,
    ctx: Context,
) -> dict[str, Any]:
    """이전에 시작했던 작업을 디스크에서 재개합니다 (서버 재시작 후 등).

    work_id → work_dir 매핑은 메모리에만 있어 MCP 서버가 재시작되면
    사라집니다. 이 도구는 고정 결과 폴더의 .work/state.json에 보존된 work_id를
    복원해 이후 도구들이 정상 동작하도록 합니다.

    공개 MCP 입력은 pdf_path 하나뿐입니다. MCP 서버 프로젝트 루트 아래의 고정 경로
    `result/<pdf_basename>`에서 작업을 찾고, 서버가 Elicitation으로 재개 승인을
    받은 뒤 등록합니다. 요청 workspace와 프로세스 cwd는 사용하지 않습니다.
    다음 단계: 남은 챕터가 있으면 get_subagent_prompts(work_id)로 워크플로를
    받아 pending 챕터만 처리, 없으면 바로 finalize_study(work_id).
    """
    capability_error = _elicitation_required(ctx)
    if capability_error is not None:
        return capability_error
    resolved = _resolve_output_dir(pdf_path)
    existing = workspace.inspect_output_dir(resolved)
    if existing["can_resume"]:
        message = "기존 작업 상태를 불러오면 남은 단계부터 계속할 수 있습니다."
        result = await ctx.elicit(message=message, schema=_ResumeSelection)
        resume_confirmed = (
            _resolve_form_choice(
                result.data.resume_confirmed,
                [dict(choice) for choice in _RESUME_CHOICES],
                error="지원하지 않는 작업 재개 선택값입니다.",
            )
            if result.action == "accept" and result.data is not None
            else False
        )
        if (
            result.action != "accept"
            or result.data is None
            or not resume_confirmed
        ):
            return _elicitation_cancelled({
                "output_dir": resolved,
                "existing_work": existing,
                "selected_action": None,
            })

    state = workspace.resume_workspace(resolved)
    return _resume_response(state)


# ---------------------------------------------------------------------------
# 2. scan_pdf
# ---------------------------------------------------------------------------

@mcp.tool(name="scan_pdf")
@_safe("scan_pdf")
async def scan_pdf(
    work_id: str,
    ctx: Context,
    scan_size: int = 30,
    force_vision: bool = False,
) -> dict[str, Any]:
    """PDF 메타 + 챕터 경계 소스(내장 목차 또는 목차 후보 이미지) + offset.

    챕터 경계는 텍스트 레이어를 신뢰하지 않고 두 소스에서만 얻습니다:
    응답.data.recommendations.primary_mode 가
      - "from_outline": PDF 내장 목차(북마크)로 챕터를 구성. suggested_chapters에
        담겨 옵니다. set_chapters를 호출하면 서버가 구성·범위를 form
        Elicitation으로 확인합니다. 목차 이미지 재분석을 선택하면
        scan_pdf(work_id, force_vision=True)로 목차 페이지를 렌더합니다.
      - "analyze_toc_from_images": 내장 목차가 없음. 응답.data.toc_page_images
        (목차 페이지 JPEG 경로, ocr_status)를 확인한 뒤 prepare_ocr와
        scan_toc_with_ocr를 호출해 OCR 텍스트를 얻으세요.
        **PDF 텍스트나 파이썬 스크립트로 목차를 추정하지 마세요.** prepare_ocr를
        호출하면 서버가 OCR 언어 Elicitation을 엽니다.
    force_vision은 외부 계약 호환용 legacy 이름이며, 현재 동작은 목차 페이지
    이미지 렌더입니다.

    init_work에서 단답형·주관식·확장형 선택 또는 선택적 학습자 정보가 미정인
    기존 작업이면 이 도구가 스캔 전에 form Elicitation을 엽니다. 선택값은 공개
    도구 인자로 받지 않습니다.

    **페이지 오프셋 + Elicitation 흐름 (필수)**:
    recommendations에 page_offset(PDF = 원문 + offset), offset_confidence,
    각 챕터의 pdf_pages(PDF 페이지)·source_pages(원문 페이지)와
    next_step_guidance가 담깁니다.
    응답.data.set_chapters_next_step은 에이전트가 구성해야 할 chapters만 요구합니다.
    챕터 구성·범위, 본문 추출 방식, 실행 방식은 set_chapters 호출 중 서버가 각각
    별도의 Elicitation으로 확인합니다.
    """
    capability_error = _elicitation_required(ctx)
    if capability_error is not None:
        return capability_error
    state = workspace.load_state(work_id)
    setup = _question_setup_payload(state)
    selected: dict[str, Any] = {}
    if setup["pending_fields"] or setup["user_context_request"]:
        selected = await _elicit_question_setup(ctx, setup)
        if selected is None:
            return _elicitation_cancelled({"question_setup": setup})
    enable_short_answer = selected.get("enable_short_answer")
    enable_reflection = selected.get("enable_reflection")
    enable_extension = selected.get("enable_extension")
    user_context = selected.get("user_context")
    supplied = {
        "enable_short_answer": enable_short_answer,
        "enable_reflection": enable_reflection,
        "enable_extension": enable_extension,
    }
    missing = [
        field for field in setup["pending_fields"]
        if supplied.get(field) is None
    ]
    invalid = [
        field for field, value in supplied.items()
        if value is not None and not isinstance(value, bool)
    ]
    if user_context is not None and not isinstance(user_context, str):
        invalid.append("user_context")
    if missing or invalid:
        return _err(
            "문제 유형 선택이 빠졌거나 형식이 올바르지 않아 PDF를 스캔하지 않았습니다. "
            "같은 scan_pdf 호출을 다시 실행해 사용자의 명시적 답을 받으세요.",
            data={
                "missing": missing,
                "invalid": invalid,
            },
            next_action=_question_setup_next_action(work_id, setup),
        )

    try:
        state = workspace.confirm_question_setup(
            work_id,
            enable_short_answer=enable_short_answer,
            enable_reflection=enable_reflection,
            enable_extension=enable_extension,
            user_context=user_context,
        )
    except ValueError as e:
        setup = _question_setup_payload(state)
        return _err(
            str(e),
            next_action=(
                _question_setup_next_action(work_id, setup)
                if setup["pending_fields"] or setup["user_context_request"]
                else None
            ),
        )

    data = analysis.scan_pdf_impl(
        work_id, scan_size=scan_size, force_vision=force_vision,
    )
    data["question_options"] = state["question_options"]
    data["user_context"] = state.get("user_context", "")
    rec = _without_choice_fallback(data.get("recommendations", {}))
    data["recommendations"] = rec
    if rec.get("rejected"):
        return _err(rec.get("reason") or "scan rejected", data=data)
    text_quality = data.get("text_quality") or workspace.load_state(work_id).get("text_quality")
    set_chapters_step = _set_chapters_next_step(text_quality)
    data["set_chapters_next_step"] = set_chapters_step
    ocr_needed = (
        rec.get("primary_mode") == "analyze_toc_from_images"
        or text_quality in ("garbled", "no_text_layer")
    )
    if ocr_needed:
        data["next_step"] = _prepare_ocr_next_step()
        next_action = (
            f'prepare_ocr(work_id="{work_id}")를 호출하면 서버가 OCR 언어 '
            "Elicitation을 엽니다. "
            + (
                f'그 후 scan_toc_with_ocr(work_id="{work_id}")로 toc_page_images[].ocr_text를 '
                "얻고, path 이미지와 함께 확인해 chapters를 구성하세요."
                if rec.get("primary_mode") == "analyze_toc_from_images"
                else "OCR 모델 준비 뒤 set_chapters의 OCR 처리 모드를 선택하세요."
            )
        )
    else:
        data["next_step"] = set_chapters_step
        next_action = (
            f'set_chapters(work_id="{work_id}", chapters=recommendations.suggested_chapters, '
            "book_info={...})를 호출하면 서버가 챕터 범위·본문 추출·실행 방식 "
            "Elicitation을 차례로 엽니다."
        )
    return _without_choice_fallback(_ok(data, next_action=next_action))


# ---------------------------------------------------------------------------
# 3. prepare_ocr / scan_toc_with_ocr
# ---------------------------------------------------------------------------

@mcp.tool(name="prepare_ocr")
@_safe("prepare_ocr")
async def prepare_ocr(work_id: str, ctx: Context) -> dict[str, Any]:
    """PaddleOCR CPU 모델을 준비합니다.

    공개 MCP 입력은 work_id뿐이며 서버가 한국어/영어를 form Elicitation으로
    확인합니다. 첫 실행에서 모델 파일 다운로드와 모델 로드가 오래 걸릴 수 있으므로,
    OCR이 필요한 흐름에서는 이 도구를 별도로 호출해 사용자에게 지연 이유를 드러냅니다.
    다음 단계: scan_toc_with_ocr(work_id) 또는 set_chapters(work_id, chapters,
    book_info). set_chapters가 본문 추출 방식과 실행 방식을 Elicitation으로 확인합니다.
    """
    capability_error = _elicitation_required(ctx)
    if capability_error is not None:
        return capability_error
    message = "PDF의 내용을 추출하기 위해 사용할 OCR 언어 모델을 선택해주세요."
    result = await ctx.elicit(message=message, schema=_OcrLanguageSelection)
    if result.action != "accept" or result.data is None:
        return _elicitation_cancelled(
            {"work_id": work_id},
        )
    ocr_language = str(_resolve_form_choice(
        result.data.ocr_language,
        [dict(choice) for choice in _OCR_LANGUAGE_CHOICES],
        error="지원하지 않는 OCR 언어입니다.",
    ))
    data = analysis.prepare_ocr_impl(work_id, ocr_language)
    data["ocr_language"] = ocr_language
    return _without_choice_fallback(_ok(data, next_action=(
        f'scan_toc_with_ocr(work_id="{work_id}") 또는 '
        f'set_chapters(work_id="{work_id}", chapters=..., book_info=...)를 호출하세요. '
        "set_chapters가 본문 추출 방식과 실행 방식을 Elicitation으로 확인합니다."
    )))


@mcp.tool()
@_safe("scan_toc_with_ocr")
def scan_toc_with_ocr(work_id: str) -> dict[str, Any]:
    """scan_pdf가 렌더한 목차 후보 이미지를 PaddleOCR CPU로 읽습니다.

    이 도구는 챕터를 자동 확정하지 않습니다. 응답의 toc_page_images[].ocr_text와
    path 이미지를 확인해 chapters를 구성한 뒤 set_chapters를 호출하세요. 처리
    모드는 set_chapters가 form Elicitation으로 직접 확인합니다.
    """
    if workspace.load_state(work_id).get("ocr_language") not in analysis.ocr.OCR_LANGUAGE_MODELS:
        return _err(
            "목차 OCR 전에 prepare_ocr의 OCR 언어 Elicitation을 완료해야 합니다.",
            next_action=f'prepare_ocr(work_id="{work_id}")',
        )
    data = analysis.scan_toc_with_ocr_impl(work_id)
    if data.get("requires_prepare_ocr"):
        return _err(
            "OCR 모델 캐시가 없어 목차 OCR을 시작하지 않았습니다. "
            "prepare_ocr(work_id)를 먼저 호출해 모델 다운로드/로드를 사용자가 볼 수 "
            "있는 단계에서 수행하세요.",
            data=_without_choice_fallback(data),
            next_action=(
                f'prepare_ocr(work_id="{work_id}")'
            ),
        )
    data["next_step"] = _set_chapters_next_step(
        workspace.load_state(work_id).get("text_quality")
    )
    return _ok(_without_choice_fallback(data), next_action=(
        f'set_chapters(work_id="{work_id}", chapters=<toc_page_images[].ocr_text와 '
        "path 이미지로 구성>, book_info={...})를 호출하면 서버가 필요한 "
        "Elicitation을 차례로 엽니다."
    ))


# ---------------------------------------------------------------------------
# 4. set_chapters
# ---------------------------------------------------------------------------

@mcp.tool(name="set_chapters")
@_safe("set_chapters")
async def set_chapters(
    work_id: str,
    chapters: list[dict[str, Any]],
    ctx: Context,
    book_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """챕터 구조 + 처리 모드를 확정하고 챕터별 본문을 추출합니다.

    - chapters: [{"chapter_id","title","pdf_pages":[start,end]}, ...] (1-based)
      pdf_pages는 항상 **PDF 페이지** 기준. source_pages(원문 페이지)는
      옵셔널 표시용 메타로, 명시적 null까지 보존하되 범위를 검증하지 않습니다.
      구형 page_range/printed_range 입력은 읽기 호환을 위해 받지만 새 응답과
      저장 데이터에는 pdf_pages/source_pages만 사용합니다.
    공개 MCP 입력은 work_id, chapters, 선택적 book_info뿐입니다. 서버는 챕터 구성
    방식, 본문 추출 방식(text/OCR), 실행 방식(sequential/parallel)을 form
    Elicitation으로 차례로 확인합니다. 직접 입력과 균등 청크는 각각 페이지 범위와
    청크 크기 form을 추가로 엽니다. OCR을 고르면 prepare_ocr에서 이미 Elicitation으로
    확정해 저장한 언어를 사용합니다.
    - 각 chapter에 optional "skip": true 를 주면 그 챕터는 본문 추출과
      sub-agent 디스패치, 렌더링 모두에서 제외됩니다. **찾아보기·색인·
      판권·저자 소개 같은 비본문 페이지가 섞여 들어왔을 때 사용**하세요.
    - book_info: 메인 LLM이 PDF 메타·목차로 보강한 책 정보
    OCR 방식에서는 set_chapters 시점에 PaddleOCR CPU로 본문 텍스트를 선계산합니다.
    서브에이전트는 get_chapter_content가 반환한 text를 읽습니다.

    ※ text 모드 가드: scan_pdf가 측정한 text_quality가 "garbled"(인코딩 깨짐) 또는
      "no_text_layer"(텍스트 거의 없음)이면 text 추출이 무의미하므로 거부하고
      본문 추출 Elicitation에서 OCR 방식만 허용합니다.
    다음 단계: get_subagent_prompts(work_id)
    """
    capability_error = _elicitation_required(ctx)
    if capability_error is not None:
        return capability_error
    chapter_selection = await _elicit_chapter_setup(ctx, work_id, chapters)
    if chapter_selection is None:
        return _elicitation_cancelled({
            "chapters": chapters,
            "next_step": _set_chapters_next_step(
                workspace.load_state(work_id).get("text_quality"),
            ),
        })
    if chapter_selection.get("chapter_strategy") == "reanalyze_with_vision":
        return _err(
            "사용자가 목차 이미지 재분석을 선택해 챕터를 확정하지 않았습니다.",
            data={"selected_chapter_strategy": "reanalyze_with_vision"},
            next_action=(
                f'scan_pdf(work_id="{work_id}", force_vision=True)를 호출한 뒤 '
                "prepare_ocr → scan_toc_with_ocr 순서로 다시 구성하세요."
            ),
        )
    if chapter_selection["chapter_strategy"] == "manual_pdf_pages":
        manual_chapters = await _elicit_manual_chapters(ctx, work_id)
        if manual_chapters is None:
            return _elicitation_cancelled({
                "chapters": chapters,
                "next_step": _set_chapters_next_step(
                    workspace.load_state(work_id).get("text_quality"),
                ),
            })
        chapters = manual_chapters
    elif chapter_selection["chapter_strategy"] == "chunks":
        chunk_chapters = await _elicit_chunk_size(ctx, work_id)
        if chunk_chapters is None:
            return _elicitation_cancelled({
                "chapters": chapters,
                "next_step": _set_chapters_next_step(
                    workspace.load_state(work_id).get("text_quality"),
                ),
            })
        chapters = chunk_chapters
    extraction_mode = await _elicit_extraction_mode(ctx, work_id)
    if extraction_mode is None:
        return _elicitation_cancelled({
            "chapters": chapters,
            "next_step": _set_chapters_next_step(
                workspace.load_state(work_id).get("text_quality"),
            ),
        })
    execution_mode = await _elicit_execution_mode(ctx)
    if execution_mode is None:
        return _elicitation_cancelled({
            "chapters": chapters,
            "next_step": _set_chapters_next_step(
                workspace.load_state(work_id).get("text_quality"),
            ),
        })
    if execution_mode not in processing_mode_contract.VALID_EXECUTION_MODES or \
            extraction_mode not in processing_mode_contract.VALID_EXTRACTION_MODES:
        # 앞선 Elicitation이 값을 보장하므로 여기서는 실행 전 불변식만 검사한다.
        try:
            tq = workspace.load_state(work_id).get("text_quality")
        except Exception:
            tq = None
        return _err(
            processing_mode_contract.invalid_mode_message(tq),
            data=processing_mode_contract.invalid_mode_data(tq),
        )

    # text 모드 가드: 텍스트 레이어가 깨졌거나(garbled) 사실상 없으면(no_text_layer)
    # 라이브러리 추출 본문이 쓰레기가 된다 → OCR로 강제 전환하도록 거부한다.
    # scan_pdf가 이미 20p 샘플로 text_quality(mojibake 판정 포함)를 계산해 state에
    # 저장해 두므로 페이지를 다시 읽지 않고 그 값만 본다.
    if extraction_mode == "text":
        tq = workspace.load_state(work_id).get("text_quality")
        if tq in ("garbled", "no_text_layer"):
            reason = (
                "텍스트 레이어 인코딩이 깨져 있어(mojibake)"
                if tq == "garbled"
                else "텍스트 레이어가 거의 없어"
            )
            return _err(
                f"이 PDF는 {reason} text 모드 추출 결과를 신뢰할 수 없습니다 "
                f"(text_quality={tq}). 같은 set_chapters 호출을 다시 실행해 본문 추출 "
                "Elicitation에서 OCR 방식을 선택하세요. PaddleOCR CPU로 본문을 "
                "선계산합니다.",
                data={
                    "text_quality": tq,
                    "forced_extraction_mode": "ocr",
                    "execution_mode": execution_mode,
                },
            )
    elif extraction_mode == "ocr":
        selected_language = workspace.load_state(work_id).get("ocr_language")
        if selected_language not in analysis.ocr.OCR_LANGUAGE_MODELS:
            return _err(
                "OCR 본문을 처리하려면 prepare_ocr의 OCR 언어 Elicitation을 먼저 "
                "완료해야 합니다.",
                data={
                    "execution_mode": execution_mode,
                    "extraction_mode": "ocr",
                },
                next_action=(
                    f'prepare_ocr(work_id="{work_id}") 후 '
                    "같은 set_chapters 호출을 다시 실행하세요."
                ),
            )
        if not analysis._ocr_models_cached_for_language(selected_language):
            return _err(
                "OCR 모델 캐시가 없어 본문 OCR 선계산을 시작하지 않았습니다. "
                "prepare_ocr(work_id)를 먼저 호출해 모델 다운로드/로드를 사용자가 볼 수 "
                "있는 단계에서 수행하세요.",
                data={
                    "ocr_cache": analysis._ocr_cache_status_for_language(selected_language),
                    "forced_next_step": "prepare_ocr",
                    "ocr_language": selected_language,
                },
                next_action=f'prepare_ocr(work_id="{work_id}")',
            )

    data = analysis.set_chapters_impl(
        work_id, chapters, execution_mode, extraction_mode,
        book_info=book_info,
        ocr_language=(workspace.load_state(work_id).get("ocr_language") or "korean"),
    )
    failed_chapters = data.get("failed_chapters") or []
    if extraction_mode == "ocr" and failed_chapters:
        return _err(
            "OCR 본문 선처리 중 실패한 챕터가 있어 sub-agent 처리로 넘어갈 수 없습니다. "
            "data.failed_chapters의 chapter_id, failed_pages, error를 확인한 뒤 "
            "PDF/페이지 범위/OCR 환경을 복구하고 set_chapters를 다시 호출하세요.",
            data=data,
        )

    n_skip = sum(1 for c in data["chapters"] if c.get("skipped"))
    n_body = data["chapter_count"] - n_skip
    return _without_choice_fallback(_ok(data, next_action=(
        f"본문 챕터 {n_body}개 등록"
        + (f"({n_skip}개는 skip=비본문)" if n_skip else "")
        + f". 다음: get_subagent_prompts(work_id=\"{work_id}\")로 요약/문제 "
        "프롬프트와 chapter_ids·workflow를 받으세요. 이후 챕터 처리는 반드시 "
        "**등록된 chapter_id(ch1·ch2…)** 로만 get_chapter_content를 호출하세요 — "
        "'p11-p18' 같은 페이지 범위 문자열을 chapter_id로 쓰지 마세요(특정 페이지를 "
        "보려면 scan_pdf의 toc_page_images 경로를 직접 여세요)."
    )))


# ---------------------------------------------------------------------------
# 4. get_chapter_content
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_chapter_content")
def get_chapter_content(work_id: str, chapter_id: str) -> dict[str, Any]:
    """챕터 본문을 반환합니다 (extraction_mode에 따라 형태가 다름).

    - text 모드: `text`(본문)를 반환. sub-agent는 text를 요약 생성·검토에만 씁니다.
    - ocr 모드: set_chapters에서 PaddleOCR CPU로 선계산한 `text`를 반환합니다.
    문제 생성에는 get_chapter_summary의 저장 요약만 사용합니다.
    """
    raw = analysis.get_chapter_content_impl(work_id, chapter_id)
    raw["section_candidates"] = section_source.detect_section_candidates(
        raw.get("text", ""),
    )
    # 본문을 받아간 시점 = 요약 처리 시작 → 진행 모니터링용 in_progress 마킹
    workspace.mark_chapter_in_progress(work_id, chapter_id, kind="summary")
    state = workspace.load_state(work_id)
    return _ok(raw, next_action=_pending_guidance(state, work_id, chapter_id))


# ---------------------------------------------------------------------------
# 5. get_section_content
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_section_content")
def get_section_content(
    work_id: str,
    chapter_id: str,
    section_inventory: dict[str, Any],
    section_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical 챕터 원문을 inventory의 section 경계로 무손실 분할합니다.

    section 분석자는 본문을 복사하지 않고 각 제목의 exact source_anchor만 제공합니다.
    서버가 번호형 제목 후보의 누락 여부를 먼저 감사하고, 위험한 inventory에는 독립
    section_review를 요구한 뒤 canonical text에서 span과 source_text를 계산합니다.
    """
    raw = analysis.get_chapter_content_impl(work_id, chapter_id)
    try:
        audit = section_source.audit_section_inventory(
            raw.get("text"), section_inventory,
        )
    except section_source.SectionSourceValidationError as exc:
        return _err(
            "section inventory가 원문의 번호형 제목 후보를 모두 설명하지 못했습니다. "
            "누락 후보를 실제 section으로 추가하거나 제외 근거를 candidate_exclusions에 "
            "기록한 뒤 다시 시도하세요.",
            data={
                "chapter_id": chapter_id,
                "invalid_fields": exc.invalid_fields,
                **exc.details,
            },
            next_action=(
                "get_chapter_content의 text와 section_candidates를 "
                "section_inventory_prompt로 다시 분석한 뒤 "
                f'get_section_content(work_id="{work_id}", '
                f'chapter_id="{chapter_id}", section_inventory=...)를 호출하세요.'
            ),
        )

    review_invalid = section_source.invalid_section_review_fields(
        section_review,
        required=audit["review_required"],
    )
    if review_invalid:
        return _err(
            "전체 챕터 판정 또는 후보 제외가 포함된 section inventory는 독립적인 "
            "구조 검토를 통과해야 합니다.",
            data={
                "chapter_id": chapter_id,
                "invalid_fields": review_invalid,
                "review_required": audit["review_required"],
                "section_candidates": audit["section_candidates"],
            },
            next_action=(
                "가능하면 inventory 작성자와 다른 sub-agent가 chapter text, "
                "section_inventory, section_candidates를 section_review_prompt로 검토한 "
                "뒤 passed 결과를 section_review에 넣어 "
                f'get_section_content(work_id="{work_id}", '
                f'chapter_id="{chapter_id}", section_inventory=..., '
                "section_review=...)를 다시 호출하세요. needs_revision이면 inventory를 "
                "고친 뒤 다시 검토하세요."
            ),
        )

    try:
        prepared = section_source.prepare_section_source(
            raw.get("text"), section_inventory,
        )
    except section_source.SectionSourceValidationError as exc:
        return _err(
            "section inventory를 canonical 원문 범위에 결합할 수 없습니다. "
            "invalid_fields의 anchor 또는 구조를 고친 뒤 다시 시도하세요.",
            data={
                "chapter_id": chapter_id,
                "invalid_fields": exc.invalid_fields,
            },
            next_action=(
                "get_chapter_content의 text를 section_inventory_prompt로 다시 분석한 뒤 "
                f'get_section_content(work_id="{work_id}", '
                f'chapter_id="{chapter_id}", section_inventory=...)를 호출하세요.'
            ),
        )
    prepared["chapter_id"] = chapter_id
    if isinstance(raw.get("title"), str):
        prepared["title"] = raw["title"]
    workspace.mark_chapter_in_progress(work_id, chapter_id, kind="summary")
    return _ok(
        prepared,
        next_action=(
            "이 응답의 structured_sections와 section_inventory를 summary_prompt에 "
            "전달해 초안을 작성하세요. get_chapter_content의 전체 text는 요약 입력으로 "
            "다시 전달하지 말고 review_prompt에서 최종 초안과 의미를 대조할 때만 "
            "사용하세요. review가 passed면 문제에는 summary·key_points만 전달합니다."
        ),
    )


# ---------------------------------------------------------------------------
# 6. get_chapter_summary
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_chapter_summary")
def get_chapter_summary(work_id: str, chapter_id: str) -> dict[str, Any]:
    """문제 생성용으로 검증된 요약·핵심 포인트와 원문 글자 수만 반환합니다.

    원문 text는 반환하지 않습니다. summary_status가 completed가 아니거나 저장된
    요약이 없거나 비어 있으면 실패합니다.
    """
    state = workspace.load_state(work_id)
    basis = _chapter_summary_basis(work_id, chapter_id, state)
    workspace.mark_chapter_in_progress(work_id, chapter_id, kind="extension")
    return _ok(
        basis,
        next_action=(
            "이 응답의 summary·key_points·source_char_count만 extension_prompt에 "
            "전달해 확장 문제를 만든 뒤 "
            f'save_extension_result(work_id="{work_id}", '
            f'chapter_id="{chapter_id}", data=...)로 저장하세요. 원문 text를 다시 '
            "읽거나 전달하지 마세요."
        ),
    )


# ---------------------------------------------------------------------------
# 7. get_subagent_prompts
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_subagent_prompts")
def get_subagent_prompts(work_id: str) -> dict[str, Any]:
    """sub-agent 시스템 프롬프트 + 워크플로 지시문을 반환합니다.

    응답.data.workflow_instructions를 따라 chapter_ids를 순회하세요.
    Claude Code의 Task tool, Codex의 /agent, Gemini의 task delegation,
    또는 메인 LLM이 직접 처리 — 어느 쪽이든 같은 프롬프트를 사용.
    """
    state = workspace.load_state(work_id)
    if state.get("phases", {}).get("chapter_setup") != "completed":
        data: dict[str, Any] = {
            "chapter_setup": state.get("phases", {}).get("chapter_setup"),
            "scanning": state.get("phases", {}).get("scanning"),
        }
        outline = workspace.load_outline(work_id)
        if outline and outline.get("recommendations"):
            data["recommendations"] = _without_choice_fallback(
                outline["recommendations"],
            )
            data["next_step"] = _set_chapters_next_step(state.get("text_quality"))
        return _err(
            "챕터 설정이 완료되지 않아 sub-agent 프롬프트를 만들 수 없습니다.",
            data=data,
            next_action=(
                "scan_pdf 또는 scan_toc_with_ocr 응답에서 chapters를 구성한 뒤 "
                f'set_chapters(work_id="{work_id}", chapters=..., book_info=...)를 '
                "호출하세요. 서버가 챕터 범위, 본문 추출 방식, 실행 방식을 각각 "
                "Elicitation으로 확인합니다."
            ),
        )
    pending = workspace.pending_chapters_from_state(state)
    summary_pending_ids = set(pending["summary_pending"])
    invalid = analysis.validate_chapter_raw_inputs(
        work_id,
        state,
        chapter_ids=summary_pending_ids,
    )
    if invalid:
        failed_chapters = _failed_chapters_from_invalid(invalid)
        return _err(
            "sub-agent 입력 raw 본문이 준비되지 않았거나 손상됐습니다. "
            "각 summary pending 챕터는 chapters_raw/{chapter_id}.json에 비어 있지 않은 "
            "text와 정확한 char_count가 있어야 합니다. 확장 문제만 남은 챕터는 "
            "저장된 요약을 사용합니다. OCR 실패 챕터는 먼저 "
            "set_chapters/OCR 단계를 복구한 뒤 다시 호출하세요.",
            data={
                "extraction_mode": state.get("extraction_mode"),
                "invalid_chapters": invalid,
                "failed_chapters": failed_chapters,
                "required_fields": ["chapter_raw.text", "chapter_raw.char_count"],
            },
        )
    extension_only_ids = set(pending["extension_pending"]) - summary_pending_ids
    invalid_summary_bases = _invalid_extension_summary_bases(
        work_id,
        state,
        extension_only_ids,
    )
    if invalid_summary_bases:
        return _err(
            "확장 문제의 입력 요약이 준비되지 않았거나 손상됐습니다. 확장 문제는 "
            "원문이 아니라 completed 상태의 저장된 summary와 key_points를 기준으로 "
            "생성해야 합니다.",
            data={
                "invalid_chapters": invalid_summary_bases,
                "required_fields": [
                    "summary_status=completed",
                    "summary",
                    "key_points",
                    "source_char_count",
                ],
            },
            next_action=(
                "해당 챕터의 요약·기본 문제를 먼저 정상 저장한 뒤 "
                f'get_subagent_prompts(work_id="{work_id}")를 다시 호출하세요.'
            ),
        )
    book_info = workspace.load_book_info(work_id)
    data = prompts.build_prompts(state, book_info)
    summary_pending = data["summary_pending_chapter_ids"]
    extension_pending = data["extension_pending_chapter_ids"]
    if not summary_pending and not extension_pending:
        return _ok(data, next_action=(
            f"처리할 pending 챕터가 없습니다. "
            f"list_pending_chapters(work_id=\"{work_id}\")를 호출해 완료 상태를 확인하고, "
            "finalize_study를 호출해 출력 형식 Elicitation을 여세요."
        ))
    pending_actions: list[str] = []
    if summary_pending:
        pending_actions.append(
            f"summary_pending_chapter_ids({summary_pending})는 section_inventory_prompt → "
            "조건부 section_review_prompt → get_section_content → summary_prompt → "
            "review_prompt로 요약을 확정한 뒤 "
            "basic_question_prompt에는 요약만 전달하고, 합친 결과를 "
            "save_chapter_result로 저장하세요"
        )
    if extension_pending:
        pending_actions.append(
            f"extension_pending_chapter_ids({extension_pending})는 "
            "get_chapter_summary의 요약만 extension_prompt에 전달해 생성하고 "
            "save_extension_result로 저장하세요"
        )
    return _ok(data, next_action=(
        f"workflow_instructions를 따라 chapter_ids({data['chapter_ids']})를 순회하세요. "
        + ". ".join(pending_actions)
        + ". 각 챕터는 두 목록의 포함 여부에 따른 "
        "결과별 action만 수행합니다. chapter_id는 반드시 위 목록의 값(ch1·ch2…)을 쓰고, "
        "페이지 범위 문자열은 쓰지 마세요. mode가 'ocr'이어도 선계산된 text는 "
        "요약 생성·검토까지만 사용하며 문제 생성 단계에는 전달하지 않습니다."
    ))


# ---------------------------------------------------------------------------
# 8. save_chapter_result
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("save_chapter_result")
def save_chapter_result(
    work_id: str,
    chapter_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """summarizer sub-agent의 챕터 결과 JSON을 저장합니다.

    스키마와 생성 순서는 get_subagent_prompts의 section_inventory_prompt,
    section_review_prompt, summary_prompt, review_prompt, basic_question_prompt에 명시.
    동시성 안전.

    저장 전 의미 coverage 증거, prompts.py의 기본 결과 JSON 스키마와 활성 문제
    유형을 검증한다.
    하나라도 어긋나면 completed로 마킹하지 않고 ok=False로 거부 — "모두 성공"이라
    단정했지만 실제로 누락된 결과가 조용히 completed 되는 것을 막는다.
    """
    try:
        state = workspace.load_state(work_id)
        _ensure_save_target(state, chapter_id)
    except (KeyError, FileNotFoundError, ValueError) as e:
        return _save_target_error(e, chapter_id)
    options = state.get("question_options", {})
    entry = state["chapters"][chapter_id]
    setup_generation = state.get("chapter_setup_generation", 0)
    try:
        chapter_raw = workspace.get_chapter_raw(work_id, chapter_id)
    except (FileNotFoundError, OSError, ValueError) as e:
        return _err(
            f"챕터 원문을 읽을 수 없어 요약 근거를 검증할 수 없습니다: {e}",
            data={"missing": ["chapter_raw.text"], "chapter_id": chapter_id},
        )
    if not isinstance(chapter_raw, dict):
        return _err(
            "챕터 원문 형식이 올바르지 않아 요약 근거를 검증할 수 없습니다.",
            data={"missing": ["chapter_raw.text"], "chapter_id": chapter_id},
        )
    chapter_text = chapter_raw.get("text")
    if not isinstance(chapter_text, str) or not chapter_text.strip():
        return _err(
            "챕터 원문이 비어 있어 요약 근거를 검증할 수 없습니다.",
            data={"missing": ["chapter_raw.text"], "chapter_id": chapter_id},
        )
    raw_char_count = chapter_raw.get("char_count")
    if (
        type(raw_char_count) is not int
        or raw_char_count != len(chapter_text)
        or raw_char_count != entry.get("char_count")
    ):
        return _err(
            "챕터 원문의 글자 수 메타데이터가 실제 원문 또는 상태와 일치하지 않습니다.",
            data={
                "missing": ["chapter_raw.char_count"],
                "chapter_id": chapter_id,
            },
        )

    # 에이전트가 예전 프롬프트나 환각으로 body_text를 보내더라도
    # 서버의 캐시(get_chapter_content에서 추출한 text)를 덮어쓰지 않도록 제거
    data_to_save = summary_contract.normalize_summary_quality_payload(data)
    if isinstance(data_to_save, dict):
        data_to_save.pop("body_text", None)
        # 에이전트가 임의 분량을 보내도 문제 상한과 fingerprint는 canonical raw를 쓴다.
        data_to_save["source_char_count"] = raw_char_count

    data_to_save, materialization_missing = (
        question_contract.materialize_multiple_choice_options(data_to_save)
    )
    if materialization_missing:
        return _err(
            f"챕터 결과에 필수 값이 비었거나 누락됐습니다: {materialization_missing}. "
            f'save_chapter_result(work_id="{work_id}", chapter_id="{chapter_id}", '
            "data=...)로 다시 저장하세요.",
            data={"missing": materialization_missing, "chapter_id": chapter_id},
        )

    char_count = raw_char_count
    missing = question_contract.missing_summary_fields(
        data_to_save, options, chapter_id, char_count=char_count,
    )
    missing.extend(summary_contract.missing_summary_quality_fields(
        data_to_save,
    ))
    if isinstance(data_to_save, dict):
        missing.extend(section_source.invalid_source_binding_fields(
            data_to_save.get("section_inventory"), chapter_text,
        ))
    missing = list(dict.fromkeys(missing))
    if missing:
        return _err(
            f"챕터 결과에 필수 값이 비었거나 누락됐습니다: {missing}. "
            "요약(summary)·핵심포인트(key_points)·활성 문제와 함께, 전체 본문에서 "
            "작성에 사용한 section_inventory 및 전체 원문에서 중요한 누락·왜곡 없이 "
            "passed된 summary_review를 채워 "
            f'save_chapter_result(work_id="{work_id}", chapter_id="{chapter_id}", '
            "data=...)로 다시 저장하세요. review가 needs_revision이면 먼저 요약을 "
            "보완하고 전체 text와 다시 대조하세요.",
            data={"missing": missing, "chapter_id": chapter_id},
        )
    try:
        path = workspace.save_chapter_result(
            work_id,
            chapter_id,
            data_to_save,
            expected_setup_generation=setup_generation,
        )
    except question_contract.QuestionContractError as e:
        return _err(
            f"챕터 결과의 문제 ID 또는 개수가 유효하지 않습니다: {e.missing}.",
            data={"missing": e.missing, "chapter_id": chapter_id},
        )
    except (KeyError, ValueError, RuntimeError, OSError) as e:
        return _save_target_error(e, chapter_id)
    saved_state = workspace.load_state(work_id)
    return _ok(
        {"saved_path": str(path)},
        next_action=(
            f"{chapter_id} 요약/문제 저장 완료. "
            + _pending_guidance(saved_state, work_id, chapter_id)
        ),
    )


# ---------------------------------------------------------------------------
# 7. save_extension_result
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("save_extension_result")
def save_extension_result(
    work_id: str,
    chapter_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """extension sub-agent의 결과 JSON을 저장합니다. 동시성 안전.

    저장 전 prompts.py의 확장 결과 JSON 스키마를 검증한다(빈 결과나 불완전한
    결과가 completed로 조용히 마킹되는 것 방지).
    """
    try:
        state = workspace.load_state(work_id)
        _ensure_save_target(state, chapter_id)
    except (KeyError, FileNotFoundError, ValueError) as e:
        return _save_target_error(e, chapter_id)
    if not state.get("question_options", {}).get("extension"):
        return _err(
            "이 작업은 확장 문제를 사용하지 않습니다.",
            data={"missing": ["question_options.extension"], "chapter_id": chapter_id},
        )

    data_to_save = dict(data) if isinstance(data, dict) else data
    if isinstance(data_to_save, dict):
        data_to_save.pop("body_text", None)

    char_count = state["chapters"][chapter_id].get("char_count")
    missing = question_contract.missing_extension_fields(
        data_to_save, chapter_id, char_count=char_count,
    )
    if missing:
        return _err(
            "확장 결과에 questions.extension(비어있지 않은 배열)이 필요합니다. "
            f'save_extension_result(work_id="{work_id}", chapter_id="{chapter_id}", '
            "data=...)로 확장 문제를 채워 다시 저장하세요.",
            data={"missing": missing, "chapter_id": chapter_id},
        )
    data_to_save = {
        "chapter_id": data_to_save.get("chapter_id", chapter_id),
        "questions": {
            "extension": [
                {key: item[key] for key in ("id", "question", "model_answer")}
                for item in data_to_save["questions"]["extension"]
            ],
        },
    }
    try:
        path = workspace.save_extension_result(work_id, chapter_id, data_to_save)
    except question_contract.QuestionContractError as e:
        return _err(
            f"확장 문제의 ID 또는 개수가 유효하지 않습니다: {e.missing}.",
            data={"missing": e.missing, "chapter_id": chapter_id},
        )
    except (KeyError, ValueError, RuntimeError, OSError) as e:
        return _save_target_error(e, chapter_id)
    saved_state = workspace.load_state(work_id)
    return _ok(
        {"saved_path": str(path)},
        next_action=(
            f"{chapter_id} 확장 문제 저장 완료. "
            + _pending_guidance(saved_state, work_id, chapter_id)
        ),
    )


# ---------------------------------------------------------------------------
# 8. get_work_state
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_work_state")
def get_work_state(work_id: str) -> dict[str, Any]:
    """state.json 전체를 반환합니다. 진행 상황/실패 챕터 확인용."""
    state = workspace.load_state(work_id)
    return _ok(state, next_action=(
        f"현재 단계: {state.get('current_phase')}. chapters[*].summary_status/"
        "extension_status로 진행을 확인하세요. 남은 작업은 "
        f"list_pending_chapters(work_id=\"{work_id}\")로 집계하고, 다음 호출은 그 "
        "결과에 따르세요(남았으면 처리, 없으면 finalize_study)."
    ))


# ---------------------------------------------------------------------------
# 9. list_pending_chapters
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("list_pending_chapters")
def list_pending_chapters(work_id: str) -> dict[str, Any]:
    """summary/extension이 아직 완료되지 않은 챕터 ID 목록.

    재시도 루프에서 사용. extension이 비활성이면 extension_pending은 무시한다.
    챕터 설정이 완료되고 두 pending 목록이 모두 비면 data.next_step은 선택
    파라미터 없이 finalize_study 호출을 안내한다. 출력 형식은 해당 도구가
    Elicitation으로 확인한다.
    """
    state = workspace.load_state(work_id)
    pending = workspace.pending_chapters_from_state(state)
    opts = state.get("question_options", {})
    summary_pending = pending["summary_pending"]
    ext_pending = pending["extension_pending"]
    data = {
        "summary_pending": summary_pending,
        "extension_pending": ext_pending,
        "extension_enabled": bool(opts.get("extension")),
    }
    if _ready_to_finalize(state):
        data["next_step"] = _finalize_next_step()
    return _ok(data, next_action=_pending_guidance(state, work_id))


# ---------------------------------------------------------------------------
# 10. finalize_study
# ---------------------------------------------------------------------------

@mcp.tool(name="finalize_study")
@_safe("finalize_study")
async def finalize_study(
    work_id: str,
    ctx: Context,
) -> dict[str, Any]:
    """학습 자료를 고정 결과 폴더에 렌더링합니다.

    공개 MCP 입력은 work_id뿐이며, 서버가 form Elicitation으로 최종 결과 형식을
    확인한 뒤 중간 작업 폴더를 보존한 채 렌더링합니다.
    지원 형식:
        - html: 정적 사이트 (브라우저로 열람)
        - md_tui: 챕터별 폴더 + summary.md + 학습 TUI
    응답의 next_action에 학습 자료 실행 방법이 포함됩니다.
    - html: 생성된 폴더에서 macOS/Linux는 `start_study.sh`, Windows는
      `start_study.bat`을 더블클릭합니다. 런처가 사용 가능한 포트를 선택해 진도
      API 서버와 브라우저를 시작합니다.
    - md_tui: `python3 study_tui.py`(터미널 TUI). rich가 없으면 자동 설치 시도,
      불가능한 환경이면 평문 모드로 폴백(항상 실행). 진도는 각 챕터
      progress.json에 직접 저장(서버 불필요).
    """
    capability_error = _elicitation_required(ctx)
    if capability_error is not None:
        return capability_error
    message = "완료된 챕터를 선택한 형식의 학습 자료로 만듭니다."
    result = await ctx.elicit(message=message, schema=_OutputFormatSelection)
    if result.action != "accept" or result.data is None:
        return _elicitation_cancelled({"work_id": work_id})
    output_format = str(_resolve_form_choice(
        result.data.output_format,
        [dict(choice) for choice in _OUTPUT_FORMAT_CHOICES],
        error="지원하지 않는 출력 형식입니다.",
    ))
    renderer_cls = RENDERERS.get(output_format)
    if renderer_cls is None:
        return _err(
            f"unknown internal output_format: {output_format!r}",
        )

    state = workspace.load_state(work_id)
    omitted_chapters = _omitted_chapters(state)
    omitted_notice = _omitted_chapters_notice(omitted_chapters)

    output_dir = Path(state["output_dir"])

    renderer = renderer_cls()
    install_rendered_output(
        work_id,
        output_format,
        lambda staging_dir: renderer.render(work_id, staging_dir),
    )

    workspace.update_phase(work_id, "rendering", "completed")

    work_dir = workspace.get_work_dir(work_id)

    # 중간 데이터(.work) 정리 안내 — 두 포맷 공통
    work_cleanup = (
        "\n\n[작업 데이터 정리] 중간 작업 폴더(.work/: 페이지 이미지·raw·상태 파일)가 "
        f"보존되어 있습니다 ({work_dir})."
        + f" cleanup_work(work_id=\"{work_id}\")를 호출하면 서버가 삭제 여부 "
        "Elicitation을 열고, 승인된 경우에만 .work/를 제거합니다(재실행 시 캐시로 "
        "쓰려면 보존)."
    )
    cleanup_action = {
        "tool": "cleanup_work",
        "required_parameters": [],
        "desc": "최종 결과는 유지하고 이 작업의 .work 중간 데이터만 삭제합니다.",
    }

    # MCP 서버를 띄운 그 인터프리터(=의존성 rich·pymupdf가 이미 설치된 venv).
    # 이걸로 실행하면 별도 설치 없이 바로 동작한다. 다른 python으로 실행하면
    # study_tui.py가 rich 자동 설치/평문 폴백으로 대응한다.
    py = sys.executable or "python3"

    if output_format == "md_tui":
        # 터미널 TUI — 서버 없음.
        launch_cmd = f"cd {output_dir} && {py} study_tui.py"
        data = {
            "output_dir": str(output_dir),
            "format": output_format,
            "work_dir_kept": True,
            "launch_command": launch_cmd,
            "entry_script": "study_tui.py",
            "python": py,
            "cleanup_work": cleanup_action,
        }
        if omitted_chapters:
            data["omitted_chapters"] = omitted_chapters
        next_action = (
            f"학습 자료(Markdown + 터미널 TUI)가 {output_dir}에 만들어졌습니다.\n"
            f"\n[학습 시작] 다음 명령을 실행하세요:\n"
            f"  {launch_cmd}\n"
            f"루트에서 실행하면 챕터 선택 메뉴가, `cd ch1 && {py} study_tui.py`로 "
            f"실행하면 그 챕터로 바로 진입합니다. 위 명령은 **MCP 서버와 같은 "
            f"인터프리터**(의존성 rich가 이미 설치돼 있음)라 별도 설치 없이 바로 "
            f"동작합니다. 다른 python(`python3` 등)으로 실행해도 study_tui.py가 "
            f"rich 자동 설치를 시도하고, 안 되면 평문 모드로 폴백하므로 어디서든 "
            f"실행됩니다.\n"
            f"진도(답안·완료·객관식 점수)는 각 챕터 폴더의 progress.json에 자동 "
            f"저장되어, 다시 실행하면 이어서 풀 수 있습니다(서버 불필요)."
            + omitted_notice
            + work_cleanup
        )
        return _without_choice_fallback(_ok(data, next_action=next_action))

    # html — 정적 사이트 + 진도 API 서버(study_html.py). stdlib만 쓰므로 어떤
    # python으로도 동작하지만, 일관성을 위해 같은 인터프리터를 안내한다.
    launch_cmd = f"cd {output_dir} && {py} study_html.py"
    entry = "index.html" if (output_dir / "index.html").exists() else "main.html"
    data = {
            "output_dir": str(output_dir),
            "format": output_format,
            "work_dir_kept": True,
            "launch_command": launch_cmd,
            "python": py,
            "entry_page": entry,
            "default_url": "http://localhost:8765/" + entry,
            "launch_scripts": {
                "macos_linux": "start_study.sh",
                "windows": "start_study.bat",
            },
            "auto_port_on_script_launch": True,
            "cleanup_work": cleanup_action,
    }
    if omitted_chapters:
        data["omitted_chapters"] = omitted_chapters
    return _without_choice_fallback(_ok(
        data,
        next_action=(
            f"학습 자료가 {output_dir}에 만들어졌습니다.\n"
            f"\n[학습 시작] 결과 폴더에서 macOS/Linux는 `start_study.sh`, "
            f"Windows는 `start_study.bat`을 더블클릭하세요. 스크립트가 사용 가능한 "
            f"포트를 자동으로 선택하고 브라우저를 엽니다.\n"
            f"\n[서버 종료] 스크립트가 연 서버 창을 닫거나 그 창에서 Ctrl+C 를 "
            f"누르세요. 브라우저 탭/창을 닫는 것만으로는 서버가 꺼지지 않습니다."
            + omitted_notice
            + work_cleanup
        ),
    ))


# ---------------------------------------------------------------------------
# 11. cleanup_work
# ---------------------------------------------------------------------------

@mcp.tool(name="cleanup_work")
@_safe("cleanup_work")
async def cleanup_work(work_id: str, ctx: Context) -> dict[str, Any]:
    """완료된 학습 결과는 유지하고 해당 작업의 `.work` 중간 데이터만 삭제합니다.

    `finalize_study`가 성공해 렌더링이 완료된 작업에만 사용할 수 있습니다. 결과 파일,
    manifest, 사용자 파일은 건드리지 않으며 렌더링도 다시 실행하지 않습니다.
    """
    capability_error = _elicitation_required(ctx)
    if capability_error is not None:
        return capability_error
    message = (
        "삭제 후에는 이 중간 상태로 작업을 재개할 수 없습니다. "
        "최종 학습 자료와 진도는 유지됩니다."
    )
    result = await ctx.elicit(message=message, schema=_CleanupSelection)
    cleanup_confirmed = (
        _resolve_form_choice(
            result.data.cleanup_confirmed,
            [dict(choice) for choice in _CLEANUP_CHOICES],
            error="지원하지 않는 중간 작업 데이터 선택값입니다.",
        )
        if result.action == "accept" and result.data is not None
        else False
    )
    if (
        result.action != "accept"
        or result.data is None
        or not cleanup_confirmed
    ):
        return _elicitation_cancelled({
            "work_id": work_id,
            "cleanup_confirmed": False,
        })
    data = workspace.cleanup_workspace(work_id)
    return _without_choice_fallback(_ok(
        data,
        next_action=(
            "중간 작업 데이터(.work/)만 삭제했습니다. 최종 학습 자료와 기존 진도는 "
            "그대로 사용할 수 있습니다."
        ),
    ))


# ---------------------------------------------------------------------------
# 12. list_study_results
# ---------------------------------------------------------------------------

@mcp.tool(name="list_study_results")
@_safe("list_study_results")
def list_study_results() -> dict[str, Any]:
    """MCP 서버의 고정 `result/*` 아래에 있는 학습 결과 경로를 조회합니다.

    입력은 없습니다. 각 절대 경로의 마지막 구성요소는 `init_work`가 정규화한
    PDF 이름입니다. 조회는 폴더를 만들거나 기존 결과와 상태를 변경하지 않습니다.
    """
    result_root = RESULT_ROOT.resolve()
    if not result_root.exists():
        result_paths: list[str] = []
    elif not result_root.is_dir():
        return _err(
            "고정 result 경로가 디렉터리가 아닙니다.",
            data={"result_root": str(result_root)},
        )
    else:
        directories = (
            entry
            for entry in result_root.iterdir()
            if (
                not entry.name.startswith(".")
                and not entry.is_symlink()
                and entry.is_dir()
            )
        )
        result_paths = [
            str(entry.resolve())
            for entry in sorted(
                directories,
                key=lambda path: (path.name.casefold(), path.name),
            )
        ]
    return _ok({
        "result_root": str(result_root),
        "result_paths": result_paths,
    })


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def main() -> None:
    """python -m pdf_learner 실행 진입점."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    mcp.run()
