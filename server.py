"""FastMCP 서버 — pdf-study-builder의 11개 도구 등록.

모든 도구는 {ok, error, data, next_action} 형식으로 응답하며,
예외는 raise하지 않고 ok=False로 변환한다 (MCP 통신 안정성).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import analysis, exa_client, prompts, workspace
from .renderer import RENDERERS

logger = logging.getLogger(__name__)

mcp = FastMCP("pdf-study-builder")


# ---------------------------------------------------------------------------
# 응답 헬퍼
# ---------------------------------------------------------------------------

def _ok(data: Any = None, next_action: str | None = None) -> dict[str, Any]:
    return {"ok": True, "error": None, "data": data, "next_action": next_action}


def _err(error: str, data: Any = None) -> dict[str, Any]:
    return {"ok": False, "error": error, "data": data, "next_action": None}


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

@mcp.tool()
@_safe("init_work")
def init_work(
    pdf_path: str,
    output_dir: str,
    execution_mode: str = "sequential",
    enable_multiple_choice: bool = True,
    enable_short_answer: bool = True,
    enable_reflection: bool = True,
    enable_extension: bool = True,
    user_context: str = "",
) -> dict[str, Any]:
    """워크스페이스를 생성하고 work_id를 발급합니다.

    - execution_mode: "sequential" (기본) | "parallel"
    - enable_*: 4가지 문제 유형 활성/비활성 (모두 False 금지)
    - user_context: 학습자 정보 (학년/배경 등). sub-agent 프롬프트에 주입.
    다음 단계: scan_pdf(work_id)
    """
    work_id = workspace.create_workspace(
        pdf_path=pdf_path,
        output_dir=output_dir,
        options={
            "multiple_choice": enable_multiple_choice,
            "short_answer": enable_short_answer,
            "reflection": enable_reflection,
            "extension": enable_extension,
        },
        user_context=user_context,
        execution_mode=execution_mode,
    )
    return _ok(
        {"work_id": work_id, "work_dir": str(workspace.get_work_dir(work_id))},
        next_action=f'scan_pdf(work_id="{work_id}", scan_size=20)',
    )


# ---------------------------------------------------------------------------
# 2. scan_pdf
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("scan_pdf")
def scan_pdf(work_id: str, scan_size: int = 20) -> dict[str, Any]:
    """PDF 메타 + 텍스트 품질 + 언어 + 본문 목차 후보 + 챕터 분리 추천.

    응답.data.recommendations.primary_mode 가 "from_toc" | "single_unit" |
    "chunks" | "ask_user" 중 하나. suggested_chapters를 그대로 set_chapters에
    넘길 수 있습니다. 추천 reason이 ocrmypdf 안내라면 텍스트 레이어가 없는
    PDF이므로 OCR 후 재시도해주세요.
    다음 단계: set_chapters(work_id, chapters, book_info)
    """
    data = analysis.scan_pdf_impl(work_id, scan_size=scan_size)
    rec = data.get("recommendations", {})
    if rec.get("rejected"):
        return _err(rec.get("reason") or "scan rejected", data=data)
    next_action = (
        f'set_chapters(work_id="{work_id}", chapters=<recommendations.suggested_chapters>, '
        f'book_info={{...}})'
    )
    return _ok(data, next_action=next_action)


# ---------------------------------------------------------------------------
# 3. set_chapters
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("set_chapters")
def set_chapters(
    work_id: str,
    chapters: list[dict[str, Any]],
    book_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """챕터 구조를 확정하고 챕터별 본문/이미지를 추출합니다.

    - chapters: [{"chapter_id","title","page_range":[start,end]}, ...] (1-based)
    - 각 chapter에 optional "skip": true 를 주면 그 챕터는 본문 추출과
      sub-agent 디스패치, 렌더링 모두에서 제외됩니다. **찾아보기·색인·
      판권·저자 소개 같은 비본문 페이지가 목차 후보에 섞여 들어왔을 때
      사용**하세요.
    - book_info: 메인 LLM이 scanned_text + PDF 메타로 보강한 책 정보
    다음 단계: get_subagent_prompts(work_id)
    """
    data = analysis.set_chapters_impl(work_id, chapters, book_info)
    return _ok(data, next_action=f'get_subagent_prompts(work_id="{work_id}")')


# ---------------------------------------------------------------------------
# 4. get_chapter_content
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_chapter_content")
def get_chapter_content(work_id: str, chapter_id: str) -> dict[str, Any]:
    """챕터의 본문 텍스트 + image_refs(절대 경로)를 반환합니다.

    sub-agent는 image_refs의 path를 멀티모달 입력으로 직접 로드해 활용하세요.
    """
    raw = workspace.get_chapter_raw(work_id, chapter_id)
    return _ok(raw)


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
    book_info = workspace.load_book_info(work_id)
    data = prompts.build_prompts(state, book_info)
    return _ok(data, next_action=(
        f"Process each chapter_id by following workflow_instructions. "
        f"For each: get_chapter_content → sub-agent → save_chapter_result"
        f"{' + save_extension_result' if data['enabled_types']['extension'] else ''}."
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
    """
    path = workspace.save_chapter_result(work_id, chapter_id, data)
    return _ok({"saved_path": str(path)})


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
    """extension sub-agent의 결과 JSON을 저장합니다. 동시성 안전."""
    path = workspace.save_extension_result(work_id, chapter_id, data)
    return _ok({"saved_path": str(path)})


# ---------------------------------------------------------------------------
# 8. search_extension_context (async)
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("search_extension_context")
async def search_extension_context(
    work_id: str,
    chapter_id: str,
    query: str,
) -> dict[str, Any]:
    """Exa Web Research MCP로 외부 자료 검색 (API key 불필요).

    Exa 호출 자체가 실패해도 ok=True + 빈 results로 응답하여 sub-agent가
    본문 지식만으로 확장 문제를 만들 수 있게 한다. 알 수 없는 work_id 같은
    클라이언트 오류만 ok=False로 변환된다.
    """
    # work_id 유효성 (registry에 없으면 KeyError → _safe가 처리)
    workspace.get_work_dir(work_id)
    result = await exa_client.search(query)
    return _ok({
        "query": query,
        "chapter_id": chapter_id,
        "results": result["results"],
        "exa_ok": result["ok"],
        "exa_error": result["error"],
    })


# ---------------------------------------------------------------------------
# 9. get_work_state
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_work_state")
def get_work_state(work_id: str) -> dict[str, Any]:
    """state.json 전체를 반환합니다. 진행 상황/실패 챕터 확인용."""
    state = workspace.load_state(work_id)
    return _ok(state)


# ---------------------------------------------------------------------------
# 10. list_pending_chapters
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("list_pending_chapters")
def list_pending_chapters(work_id: str) -> dict[str, Any]:
    """summary/extension이 아직 완료되지 않은 챕터 ID 목록.

    재시도 루프에서 사용. extension이 비활성이면 extension_pending은 무시.
    """
    state = workspace.load_state(work_id)
    pending = workspace.list_pending_chapters_impl(work_id)
    opts = state.get("question_options", {})
    return _ok({
        "summary_pending": pending["summary_pending"],
        "extension_pending": pending["extension_pending"] if opts.get("extension") else [],
        "extension_enabled": bool(opts.get("extension")),
    })


# ---------------------------------------------------------------------------
# 11. finalize_study
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("finalize_study")
def finalize_study(
    work_id: str,
    output_format: str = "html",
    keep_work_dir: bool = True,
) -> dict[str, Any]:
    """학습 자료를 output_dir에 렌더링합니다.

    - output_format: "html" (구현 완료) | "md_tui" (ROADMAP)
    - keep_work_dir: False면 .work/ 폴더 삭제

    응답의 next_action에 학습 자료 서버 기동 명령(`python serve.py`)이
    포함됩니다. **이 서버를 띄워야 답안/완료 토글이 progress/ 폴더에
    저장**되며, 파일을 직접 열면(file://) 진도 API가 동작하지 않습니다.
    """
    renderer_cls = RENDERERS.get(output_format)
    if renderer_cls is None:
        return _err(
            f"unknown output_format: {output_format!r}. "
            f"choices={list(RENDERERS)}"
        )

    state = workspace.load_state(work_id)
    output_dir = Path(state["output_dir"])

    renderer = renderer_cls()
    renderer.render(work_id, output_dir)  # NotImplementedError 시 _safe가 잡음

    workspace.update_phase(work_id, "rendering", "completed")

    if not keep_work_dir:
        work_dir = workspace.get_work_dir(work_id)
        if work_dir.exists():
            shutil.rmtree(work_dir)

    serve_cmd = f"cd {output_dir} && python3 serve.py"
    entry = "index.html" if (output_dir / "index.html").exists() else "main.html"
    return _ok(
        {
            "output_dir": str(output_dir),
            "format": output_format,
            "work_dir_kept": keep_work_dir,
            "serve_command": serve_cmd,
            "entry_page": entry,
            "default_url": "http://localhost:8765/" + entry,
        },
        next_action=(
            f"학습 자료가 {output_dir}에 만들어졌습니다.\n"
            f"\n[서버 시작] 진도 저장과 완료 토글이 동작하려면 다음 명령을 "
            f"실행하세요:\n"
            f"  {serve_cmd}\n"
            f"기본 포트는 8765이며 브라우저가 자동으로 "
            f"http://localhost:8765/{entry} 를 엽니다. 파일을 더블클릭"
            f"(file://)으로 열면 /api/progress 호출이 막혀 답안이 저장되지 "
            f"않습니다.\n"
            f"\n[서버 종료] 서버를 실행한 터미널에서 Ctrl+C 를 누르세요. "
            f"브라우저 탭/창을 닫는 것만으로는 서버가 꺼지지 않습니다. "
            f"백그라운드로 띄웠다면 `lsof -i :8765` 로 PID를 찾아 `kill <pid>` "
            f"하거나, `pkill -f \"serve.py --port 8765\"` 로 종료할 수 있습니다."
        ),
    )


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
