"""FastMCP 서버 — pdf-study-builder MCP 도구 등록.

모든 도구는 {ok, error, data, next_action} 형식으로 응답하며,
예외는 raise하지 않고 ok=False로 변환한다 (MCP 통신 안정성).
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, ConfigDict, Field, create_model

from . import analysis, processing_mode_contract, prompts, question_contract, workspace
from .renderer import RENDERERS
from .renderer.output_manager import install_rendered_output

logger = logging.getLogger(__name__)

MCP_INSTRUCTIONS = """
사용자 선택은 각 도구가 시작하는 MCP form elicitation으로만 받는다. 에이전트가
선택값을 도구 인자로 제공하거나 사용자를 대신해 응답하지 않는다. form elicitation을
지원하지 않는 클라이언트에서는 선택이 필요한 도구가 상태 변경 없이 실패한다.
출력 폴더는 MCP 서버 프로젝트 루트 아래 result/<pdf-name>으로 계산한다.
완료되었거나 진행 중인 결과 경로는 list_study_results로 조회한다.
""".strip()

mcp = FastMCP("pdf-study-builder", instructions=MCP_INSTRUCTIONS)

SERVER_ROOT = Path(__file__).resolve().parent
RESULT_ROOT = SERVER_ROOT / "result"


_OUTPUT_FORMAT_CHOICES = (
    {
        "value": "html",
        "label": "HTML",
        "desc": "정적 웹사이트 — 브라우저로 열람 + 진도 저장 서버",
    },
    {
        "value": "md_tui",
        "label": "Markdown + TUI",
        "desc": "챕터별 Markdown + 터미널 학습 TUI",
    },
)

_OCR_LANGUAGE_CHOICES = (
    {
        "value": "korean",
        "label": "한국어",
        "desc": "한국어 PDF를 한국어 OCR 모델로 읽습니다.",
    },
    {
        "value": "english",
        "label": "영어",
        "desc": "영어 PDF를 영어 OCR 모델로 읽습니다.",
    },
)


def _normalize_elicitation_json_schema(schema: dict[str, Any]) -> None:
    """Codex MCP form의 엄격한 최상위 스키마 계약에 맞춘다."""
    schema.pop("title", None)


class _ElicitationSelection(BaseModel):
    """모든 form Elicitation이 공유하는 Codex 호환 Pydantic 기반."""

    model_config = ConfigDict(
        json_schema_extra=_normalize_elicitation_json_schema,
    )


class _OutputFormatSelection(_ElicitationSelection):
    output_format: str = Field(
        description="사용자가 선택한 최종 학습 자료 형식",
        json_schema_extra={
            "enum": [choice["value"] for choice in _OUTPUT_FORMAT_CHOICES],
        },
    )


class _OcrLanguageSelection(_ElicitationSelection):
    ocr_language: str = Field(
        description="사용자가 선택한 PDF OCR 언어",
        json_schema_extra={
            "enum": [choice["value"] for choice in _OCR_LANGUAGE_CHOICES],
        },
    )


class _ResumeSelection(_ElicitationSelection):
    resume_confirmed: bool = Field(
        description="기존 pdf-study 작업을 이어서 진행할지 여부",
    )


class _CleanupSelection(_ElicitationSelection):
    cleanup_confirmed: bool = Field(
        description="최종 결과는 유지하고 .work 중간 데이터만 삭제할지 여부",
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
                "summarizer_prompt 스키마대로 요약·문제를 만들어 "
                f'save_chapter_result(work_id="{work_id}", '
                f'chapter_id="{chapter_id}", data=...)로 저장하세요'
            )
        if "extension" in kinds:
            actions.append(
                "같은 text와 extension_prompt로 확장 문제를 만들어 "
                f'save_extension_result(work_id="{work_id}", '
                f'chapter_id="{chapter_id}", data=...)로 저장하세요'
            )
        if actions:
            return (
                f"이 챕터({chapter_id})의 text를 읽고 "
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
            f"summary_pending={summary_pending}는 summarizer_prompt로 생성해 "
            "save_chapter_result로 저장하세요"
        )
    else:
        instructions.append(f"summary_pending={summary_pending}")
    if extension_pending:
        instructions.append(
            f"extension_pending={extension_pending}는 extension_prompt로 생성해 "
            "save_extension_result로 저장하세요"
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
        "question": "단답형 문제를 생성할까요?",
        "choices": [
            {
                "value": True,
                "label": "단답형 문제 포함",
                "desc": "챕터 핵심 개념을 짧은 문장으로 답하는 문제를 만듭니다.",
            },
            {
                "value": False,
                "label": "단답형 문제 제외",
                "desc": "단답형 문제를 만들지 않습니다.",
            },
        ],
    },
    {
        "field": "enable_reflection",
        "state_key": "reflection",
        "question": "주관식 문제를 생성할까요?",
        "choices": [
            {
                "value": True,
                "label": "주관식 문제 포함",
                "desc": "본문 근거를 설명하는 서술형 검증 문제를 만듭니다.",
            },
            {
                "value": False,
                "label": "주관식 문제 제외",
                "desc": "주관식 문제를 만들지 않습니다.",
            },
        ],
    },
    {
        "field": "enable_extension",
        "state_key": "extension",
        "question": "확장 문제를 생성할까요?",
        "choices": [
            {
                "value": True,
                "label": "확장 문제 포함",
                "desc": (
                    "PDF 개념을 학습자의 현실·실무 맥락과 연결하는 응용 문제를 "
                    "외부 검색 없이 만듭니다."
                ),
            },
            {
                "value": False,
                "label": "확장 문제 제외",
                "desc": "확장 문제를 만들지 않습니다.",
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
            "question": item["question"],
            "choices": [dict(choice) for choice in item["choices"]],
        }
        for item in _QUESTION_SETUP_DEFS
        if options.get(item["state_key"]) is None
    ]
    setup = {
        "pending_fields": [item["field"] for item in questions],
        "questions": questions,
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
        f'scan_pdf(work_id="{work_id}")를 호출하면 서버가 미정 문제 유형과 선택적 '
        "학습자 정보 Elicitation을 엽니다."
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


def _choice_lines(choices: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {choice['label']} ({choice.get('value', '')}): {choice['desc']}"
        for choice in choices
    )


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
    output_dir: str | None = None,
) -> dict[str, Any] | None:
    fields: dict[str, tuple[Any, Any]] = {}
    if setup["user_context_request"]:
        fields["user_context"] = (
            str,
            Field(
                default="",
                description="선택 사항: 학습 목적, 배경지식, 관심 분야, 현재 수준",
            ),
        )
    for question in setup["questions"]:
        choice_desc = " / ".join(
            f"{choice['label']}: {choice['desc']}" for choice in question["choices"]
        )
        fields[question["field"]] = (
            bool,
            Field(description=f"{question['question']} {choice_desc}"),
        )
    schema = create_model(
        "PdfStudyQuestionSetupSelection",
        __base__=_ElicitationSelection,
        **fields,
    )
    message = (
        (
            "다음 Codex workspace 기준 위치에 새 작업을 만듭니다.\n"
            f"- {output_dir}\n\n"
            if output_dir is not None else ""
        )
        + "다음 문제 유형을 사용자가 직접 선택해야 합니다. 각 항목과 설명을 그대로 "
        "확인한 뒤 답해주세요.\n"
        + "\n".join(
            f"\n{question['question']}\n{_choice_lines(question['choices'])}"
            for question in setup["questions"]
        )
    )
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    return result.data.model_dump()


async def _elicit_chapter_setup(
    ctx: Context,
    work_id: str,
    chapters: list[dict[str, Any]],
) -> dict[str, Any] | None:
    outline = workspace.load_outline(work_id) or {}
    recommendations = outline.get("recommendations") or {}
    chapter_choices = recommendations.get("user_choice_options") or []
    fields: dict[str, tuple[Any, Any]] = {
        "chapters_confirmed": (
            bool,
            Field(description="표시된 챕터 제목과 PDF 페이지 범위를 이대로 사용할지 여부"),
        ),
    }
    if chapter_choices:
        chapter_values = tuple(choice["value"] for choice in chapter_choices)
        fields["chapter_strategy"] = (
            str,
            Field(
                description="사용자가 선택한 챕터 구성 방식",
                json_schema_extra={"enum": list(chapter_values)},
            ),
        )
    schema = create_model(
        "PdfStudyChapterSetupSelection",
        __base__=_ElicitationSelection,
        **fields,
    )
    chapter_lines = []
    for chapter in chapters:
        pages = chapter.get("pdf_pages", chapter.get("page_range"))
        chapter_lines.append(
            f"- {chapter.get('chapter_id')}: {chapter.get('title')} / PDF {pages}"
        )
    message = (
        "[챕터 구성과 범위]\n"
        "챕터 구성 방식과 제목·PDF 페이지 범위를 사용자가 직접 확인해야 합니다.\n\n"
        + (
            "[챕터 구성 방식]\n"
            + _choice_lines(chapter_choices)
            + "\n\n"
            if chapter_choices else ""
        )
        + "[챕터]\n"
        + "\n".join(chapter_lines)
    )
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    selected = result.data.model_dump()
    if (
        chapter_choices
        and selected.get("chapter_strategy") not in chapter_values
    ):
        raise ValueError("지원하지 않는 챕터 구성 방식입니다.")
    return selected


async def _elicit_extraction_mode(ctx: Context, work_id: str) -> str | None:
    text_quality = workspace.load_state(work_id).get("text_quality")
    choices = processing_mode_contract.extraction_choices(text_quality)
    allowed_values = tuple(choice["value"] for choice in choices)
    schema = create_model(
        "PdfStudyExtractionModeSelection",
        __base__=_ElicitationSelection,
        extraction_mode=(
            str,
            Field(
                description="사용자가 선택한 본문 추출 방식",
                json_schema_extra={"enum": list(allowed_values)},
            ),
        ),
    )
    message = (
        "[본문 추출 방식]\n"
        + _choice_lines(choices)
        + "\nOCR 본문 선처리는 실행 방식과 별개의 서버 내부 상한으로 제한됩니다."
    )
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    selected = str(result.data.extraction_mode)
    if selected not in allowed_values:
        raise ValueError("지원하지 않는 본문 추출 방식입니다.")
    return selected


async def _elicit_execution_mode(ctx: Context) -> str | None:
    choices = processing_mode_contract.execution_choices()
    allowed_values = tuple(choice["value"] for choice in choices)
    schema = create_model(
        "PdfStudyExecutionModeSelection",
        __base__=_ElicitationSelection,
        execution_mode=(
            str,
            Field(
                description="사용자가 선택한 챕터 실행 방식",
                json_schema_extra={"enum": list(allowed_values)},
            ),
        ),
    )
    message = "[실행 방식]\n" + _choice_lines(choices)
    result = await ctx.elicit(message=message, schema=schema)
    if result.action != "accept" or result.data is None:
        return None
    selected = str(result.data.execution_mode)
    if selected not in allowed_values:
        raise ValueError("지원하지 않는 챕터 실행 방식입니다.")
    return selected


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
    get_chapter_content/save_chapter_result(+ extension이면 save_extension_result) →
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
    달라지지 않습니다. 문제 유형과 선택적 user_context는 서버가 form
    Elicitation으로 직접 받습니다. 기존 관리 작업이 있으면 재개/교체도 같은 호출
    안의 Elicitation으로 확인합니다. Elicitation을 지원하지 않으면 상태를 바꾸지
    않고 실패합니다.

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
            "고정 출력 폴더에 pdf-study가 관리하지 않는 파일이 있어 사용할 수 없습니다.",
            data={"output_dir": resolved_dir, "existing_work": existing},
        )
    if existing["kind"] != "available":
        allowed_actions = (
            ["resume", "replace"] if existing["can_resume"] else ["replace"]
        )
        action_schema = create_model(
            "PdfStudyExistingWorkActionSelection",
            __base__=_ElicitationSelection,
            action=(
                str,
                Field(
                    description="기존 출력 작업 처리 방식",
                    json_schema_extra={"enum": allowed_actions},
                ),
            ),
        )
        action_lines = []
        if existing["can_resume"]:
            action_lines.append(
                "- 기존 작업 이어가기 (resume): 기존 상태에서 남은 작업을 계속합니다."
            )
        action_lines.append(
            "- 기존 작업 교체 (replace): 같은 고정 출력 폴더에서 새로 시작합니다."
        )
        action_result = await ctx.elicit(
            message=(
                "고정 출력 폴더에 기존 pdf-study 작업이 있습니다.\n"
                + "\n".join(action_lines)
            ),
            schema=action_schema,
        )
        if action_result.action != "accept" or action_result.data is None:
            return _elicitation_cancelled({
                "output_dir": resolved_dir,
                "existing_work": existing,
            })
        action = str(action_result.data.action)
        if action not in allowed_actions:
            raise ValueError("지원하지 않는 기존 작업 처리 방식입니다.")
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
        output_dir=resolved_dir,
    )
    if selected is None:
        return _elicitation_cancelled({"output_dir": resolved_dir})
    user_context = (selected.pop("user_context", None) or "").strip()
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
        message = (
            "- 기존 작업 이어가기 (resume): 기존 .work/state.json을 등록해 "
            "남은 챕터부터 계속합니다."
        )
        result = await ctx.elicit(message=message, schema=_ResumeSelection)
        if (
            result.action != "accept"
            or result.data is None
            or not result.data.resume_confirmed
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
    message = (
        f"{_ocr_language_setup()['question']}\n"
        f"{_choice_lines([dict(choice) for choice in _OCR_LANGUAGE_CHOICES])}"
    )
    result = await ctx.elicit(message=message, schema=_OcrLanguageSelection)
    if result.action != "accept" or result.data is None:
        return _elicitation_cancelled(
            {"work_id": work_id},
        )
    ocr_language = result.data.ocr_language
    if ocr_language not in {
        choice["value"] for choice in _OCR_LANGUAGE_CHOICES
    }:
        raise ValueError("지원하지 않는 OCR 언어입니다.")
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
    공개 MCP 입력은 work_id, chapters, 선택적 book_info뿐입니다. 서버는 챕터
    구성·범위, 본문 추출 방식(text/OCR), 실행 방식(sequential/parallel)을 세 개의
    form Elicitation으로 차례로 확인합니다. OCR을 고르면 prepare_ocr에서 이미
    Elicitation으로 확정해 저장한 언어를 사용합니다.
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
    if not chapter_selection["chapters_confirmed"]:
        return _elicitation_cancelled({
            "chapters": chapters,
            "next_step": _set_chapters_next_step(
                workspace.load_state(work_id).get("text_quality"),
            ),
        })
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

    - text 모드: `text`(본문)를 반환. sub-agent는 text를 읽고 요약/문제를 만드세요.
    - ocr 모드: set_chapters에서 PaddleOCR CPU로 선계산한 `text`를 반환합니다.
    """
    raw = analysis.get_chapter_content_impl(work_id, chapter_id)
    # 본문을 받아간 시점 = 요약 처리 시작 → 진행 모니터링용 in_progress 마킹
    workspace.mark_chapter_in_progress(work_id, chapter_id, kind="summary")
    state = workspace.load_state(work_id)
    return _ok(raw, next_action=_pending_guidance(state, work_id, chapter_id))


# ---------------------------------------------------------------------------
# 5. get_subagent_prompts
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
    pending_ids = set(pending["summary_pending"]) | set(pending["extension_pending"])
    invalid = analysis.validate_chapter_raw_inputs(
        work_id,
        state,
        chapter_ids=pending_ids,
    )
    if invalid:
        failed_chapters = _failed_chapters_from_invalid(invalid)
        return _err(
            "sub-agent 입력 raw 본문이 준비되지 않았거나 손상됐습니다. "
            "각 pending 챕터(처리 대상)는 chapters_raw/{chapter_id}.json에 비어 있지 않은 "
            "text와 정확한 char_count가 있어야 합니다. OCR 실패 챕터는 먼저 "
            "set_chapters/OCR 단계를 복구한 뒤 다시 호출하세요.",
            data={
                "extraction_mode": state.get("extraction_mode"),
                "invalid_chapters": invalid,
                "failed_chapters": failed_chapters,
                "required_fields": ["chapter_raw.text", "chapter_raw.char_count"],
            },
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
            f"summary_pending_chapter_ids({summary_pending})는 summarizer_prompt로 "
            "생성해 save_chapter_result로 저장하세요"
        )
    if extension_pending:
        pending_actions.append(
            f"extension_pending_chapter_ids({extension_pending})는 extension_prompt로 "
            "생성해 save_extension_result로 저장하세요"
        )
    return _ok(data, next_action=(
        f"workflow_instructions를 따라 chapter_ids({data['chapter_ids']})를 순회하세요. "
        + ". ".join(pending_actions)
        + ". 각 챕터는 두 목록의 포함 여부에 따른 "
        "결과별 action만 수행합니다. chapter_id는 반드시 위 목록의 값(ch1·ch2…)을 쓰고, "
        "페이지 범위 문자열은 "
        "쓰지 마세요. mode가 'ocr'이어도 set_chapters에서 선계산된 text를 읽습니다."
    ))


# ---------------------------------------------------------------------------
# 6. save_chapter_result
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("save_chapter_result")
def save_chapter_result(
    work_id: str,
    chapter_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """summarizer sub-agent의 챕터 결과 JSON을 저장합니다.

    스키마는 get_subagent_prompts의 summarizer_prompt에 명시. 동시성 안전.

    저장 전 prompts.py의 기본 결과 JSON 스키마와 활성 문제 유형을 검증한다.
    하나라도 어긋나면 completed로 마킹하지 않고 ok=False로 거부 — "모두 성공"이라
    단정했지만 실제로 누락된 결과가 조용히 completed 되는 것을 막는다.
    """
    try:
        state = workspace.load_state(work_id)
        _ensure_save_target(state, chapter_id)
    except (KeyError, FileNotFoundError, ValueError) as e:
        return _save_target_error(e, chapter_id)
    options = state.get("question_options", {})

    # 에이전트가 예전 프롬프트나 환각으로 body_text를 보내더라도
    # 서버의 캐시(get_chapter_content에서 추출한 text)를 덮어쓰지 않도록 제거
    data_to_save = dict(data) if isinstance(data, dict) else data
    if isinstance(data_to_save, dict):
        data_to_save.pop("body_text", None)

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

    char_count = state["chapters"][chapter_id].get("char_count")
    missing = question_contract.missing_summary_fields(
        data_to_save, options, chapter_id, char_count=char_count,
    )
    if missing:
        return _err(
            f"챕터 결과에 필수 값이 비었거나 누락됐습니다: {missing}. "
            "요약(summary)·핵심포인트(key_points)와 활성화된 문제 유형을 모두 채워 "
            f'save_chapter_result(work_id="{work_id}", chapter_id="{chapter_id}", '
            "data=...)로 다시 저장하세요. (모두 성공했다고 단정하기 전에 각 필드를 "
            "직접 확인하세요.)",
            data={"missing": missing, "chapter_id": chapter_id},
        )
    try:
        path = workspace.save_chapter_result(work_id, chapter_id, data_to_save)
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
    message = (
        "최종 학습 자료 형식을 선택하세요.\n"
        f"{_choice_lines([dict(choice) for choice in _OUTPUT_FORMAT_CHOICES])}"
    )
    result = await ctx.elicit(message=message, schema=_OutputFormatSelection)
    if result.action != "accept" or result.data is None:
        return _elicitation_cancelled({"work_id": work_id})
    output_format = result.data.output_format
    if output_format not in {
        choice["value"] for choice in _OUTPUT_FORMAT_CHOICES
    }:
        raise ValueError("지원하지 않는 출력 형식입니다.")
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
        "최종 결과는 유지하고 이 작업의 .work 중간 데이터만 삭제합니다. "
        "삭제 후에는 이 중간 상태로 작업을 재개할 수 없습니다."
    )
    result = await ctx.elicit(message=message, schema=_CleanupSelection)
    if (
        result.action != "accept"
        or result.data is None
        or not result.data.cleanup_confirmed
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
    """python -m pdf_study 실행 진입점."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    mcp.run()
