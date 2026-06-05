"""FastMCP 서버 — pdf-study-builder의 12개 도구 등록.

모든 도구는 {ok, error, data, next_action} 형식으로 응답하며,
예외는 raise하지 않고 ok=False로 변환한다 (MCP 통신 안정성).
"""
from __future__ import annotations

import logging
import re
import shutil
import sys
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


_SAFE_NAME_RE = re.compile(r"[^\w가-힣.\-]+")  # 영숫자 / 한글 / _ . - 외엔 치환


def _pdf_name_slug(pdf_path: str) -> str:
    """PDF 파일명을 디렉토리 이름으로 안전하게 정규화."""
    stem = Path(pdf_path).stem
    safe = _SAFE_NAME_RE.sub("_", stem)
    safe = re.sub(r"_+", "_", safe).strip("_.")
    return safe or "study"


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
    output_dir: str = "",
    execution_mode: str = "",
    extraction_mode: str = "",
    enable_multiple_choice: bool = True,
    enable_short_answer: bool = True,
    enable_reflection: bool = True,
    enable_extension: bool = True,
    user_context: str = "",
) -> dict[str, Any]:
    """워크스페이스를 생성하고 work_id를 발급합니다.

    - output_dir: 학습 자료를 저장할 디렉토리. 비워두면 현재 작업 디렉토리
      아래에 `result/<pdf_basename>/` 형태로 자동 생성됩니다 (PDF 파일명에서
      안전하지 않은 문자는 `_`로 치환). 같은 PDF로 재실행하면 같은 폴더에
      **덮어씌워지므로**, 이전 결과를 보존하려면 명시적으로 다른 경로를 주세요.
    - execution_mode: "sequential" | "parallel". **기본값 없음 — 임의로 정하지
      말고 반드시 사용자에게 물어 선택을 받으세요.** 미지정 시 거부됩니다.
        - sequential: 한 챕터씩 순차 처리 (안정적, 느림)
        - parallel: 최대 5개 챕터 동시 처리 (빠름, sub-agent 병렬 디스패치)
    - extraction_mode: "text" | "ocr". **기본값 없음 — 임의로 정하지 말고 반드시
      사용자에게 물어 선택을 받으세요.** 미지정 시 거부됩니다.
        - text: 디지털/전자책 PDF(텍스트 복사·검색이 잘 되는 PDF)에 적합.
          빠르고 비용 적음. 단 스캔본·글꼴 깨진 PDF는 본문이 손상될 수 있음.
        - ocr: 스캔본·이미지 기반·텍스트가 깨지는 PDF에 적합. 비전 LLM(sub-agent)이
          페이지 이미지를 직접 읽어 정확하나 느리고 비용 큼.
    - enable_*: 4가지 문제 유형 활성/비활성 (모두 False 금지)
    - user_context: 학습자 정보 (학년/배경 등). sub-agent 프롬프트에 주입.
    다음 단계: scan_pdf(work_id)
    """
    if execution_mode not in ("sequential", "parallel"):
        return _err(
            "execution_mode가 지정되지 않았습니다. 기본값을 임의로 정하지 말고, "
            "사용자에게 '직렬(sequential): 한 챕터씩 순차 처리 / "
            "병렬(parallel): 최대 5개 챕터 동시 처리' 중 무엇을 원하는지 물어본 뒤 "
            "그 선택을 execution_mode로 전달해 다시 호출하세요.",
            data={"choices": ["sequential", "parallel"]},
        )
    if extraction_mode not in ("text", "ocr"):
        return _err(
            "extraction_mode가 지정되지 않았습니다. 기본값을 임의로 정하지 말고, "
            "사용자에게 '텍스트(text): 디지털 PDF에서 라이브러리로 텍스트 추출 — "
            "빠르고 저렴하나 스캔본·글꼴 깨진 PDF는 본문 손상 위험 / "
            "OCR(ocr): 비전 LLM이 페이지 이미지를 직접 읽음 — 스캔본·깨진 PDF에 "
            "강하나 느리고 비용 큼' 중 무엇을 원하는지 물어본 뒤 그 선택을 "
            "extraction_mode로 전달해 다시 호출하세요.",
            data={"choices": ["text", "ocr"]},
        )

    work_id = workspace.make_work_id()
    resolved_dir = (output_dir or "").strip()
    if not resolved_dir:
        resolved_dir = str(Path.cwd() / "result" / _pdf_name_slug(pdf_path))

    workspace.create_workspace(
        pdf_path=pdf_path,
        output_dir=resolved_dir,
        options={
            "multiple_choice": enable_multiple_choice,
            "short_answer": enable_short_answer,
            "reflection": enable_reflection,
            "extension": enable_extension,
        },
        user_context=user_context,
        execution_mode=execution_mode,
        extraction_mode=extraction_mode,
        work_id=work_id,
    )
    return _ok(
        {
            "work_id": work_id,
            "work_dir": str(workspace.get_work_dir(work_id)),
            "output_dir": resolved_dir,
        },
        next_action=f'scan_pdf(work_id="{work_id}", scan_size=30)',
    )


# ---------------------------------------------------------------------------
# 1b. resume_work
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("resume_work")
def resume_work(output_dir: str = "", pdf_path: str = "") -> dict[str, Any]:
    """이전에 시작했던 작업을 디스크에서 재개합니다 (서버 재시작 후 등).

    work_id → work_dir 매핑은 메모리에만 있어 MCP 서버가 재시작되면
    사라집니다. 이 도구는 <output_dir>/.work/state.json에 보존된 work_id를
    복원해 이후 도구들이 정상 동작하도록 합니다.

    - output_dir: 재개할 작업의 output_dir. 주면 그 폴더의 .work/를 사용.
    - pdf_path: output_dir을 비우면 init_work과 동일한 규칙
      (<cwd>/result/<pdf_basename>)으로 추론합니다.
    둘 중 하나는 반드시 필요합니다.
    다음 단계: 남은 챕터가 있으면 get_subagent_prompts(work_id)로 워크플로를
    받아 pending 챕터만 처리, 없으면 바로 finalize_study(work_id).
    """
    resolved = (output_dir or "").strip()
    if not resolved:
        if not (pdf_path or "").strip():
            return _err("output_dir 또는 pdf_path 중 하나는 필요합니다.")
        resolved = str(Path.cwd() / "result" / _pdf_name_slug(pdf_path))

    state = workspace.resume_workspace(resolved)
    work_id = state["work_id"]
    pending = workspace.list_pending_chapters_impl(work_id)
    opts = state.get("question_options", {})
    ext_pending = pending["extension_pending"] if opts.get("extension") else []
    has_pending = bool(pending["summary_pending"] or ext_pending)

    return _ok(
        {
            "work_id": work_id,
            "output_dir": state.get("output_dir"),
            "current_phase": state.get("current_phase"),
            "execution_mode": state.get("execution_mode"),
            "extraction_mode": state.get("extraction_mode"),
            "summary_pending": pending["summary_pending"],
            "extension_pending": ext_pending,
        },
        next_action=(
            f'get_subagent_prompts(work_id="{work_id}") 로 워크플로를 받아 '
            "summary_pending/extension_pending 챕터만 처리한 뒤 "
            "finalize_study를 호출하세요."
            if has_pending
            else f'남은 챕터가 없습니다. finalize_study(work_id="{work_id}")로 진행하세요.'
        ),
    )


# ---------------------------------------------------------------------------
# 2. scan_pdf
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("scan_pdf")
def scan_pdf(
    work_id: str,
    scan_size: int = 30,
    allow_garbled: bool = False,
) -> dict[str, Any]:
    """PDF 메타 + 텍스트 품질 + 언어 + 본문 목차 후보 + 챕터 분리 추천.

    응답.data.recommendations.primary_mode 가 "from_toc" | "single_unit" |
    "chunks" | "ask_user" 중 하나.

    **페이지 오프셋 + 3택 흐름 (필수)**:
    recommendations에 page_offset(물리 = 책 + offset), offset_confidence,
    각 suggested_chapter의 page_range(PDF 물리)·printed_range(책 페이지),
    user_choices, next_step_guidance가 담깁니다. next_step_guidance를 따라
    분석된 챕터를 **PDF·책 페이지 둘 다** 표기해 사용자에게 보여주고 반드시
    ① 이대로 진행 ② 직접 입력(반드시 PDF 물리 페이지로 받기) ③ 청크 단위
    중 선택을 받으세요. offset_confidence가 high가 아니거나 from_toc 경계가
    의심되면 첫 챕터 제목이 계산된 PDF 페이지에 실제 나오는지 본문을 읽어
    보정하세요.

    추천 reason이 ocrmypdf 안내라면 텍스트 레이어가 없는 PDF이므로 OCR 후
    재시도해주세요. 인코딩이 깨진(모지바케) PDF면 거부되며 text_sample에
    샘플이 담깁니다 — ① 무손실 재추출 ② OCR ③ 그대로 진행(allow_garbled=True
    재호출) 중 선택을 받으세요.
    다음 단계: set_chapters(work_id, chapters, book_info)
    """
    data = analysis.scan_pdf_impl(
        work_id, scan_size=scan_size, allow_garbled=allow_garbled,
    )
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
    language: str = "",
) -> dict[str, Any]:
    """챕터 구조를 확정하고 챕터별 본문/이미지를 추출합니다.

    - chapters: [{"chapter_id","title","page_range":[start,end]}, ...] (1-based)
      page_range는 항상 **PDF 물리 페이지** 기준. printed_range(책 페이지)는
      옵셔널 표시용 메타로, 주면 보존하되 검증하지 않습니다.
    - 각 chapter에 optional "skip": true 를 주면 그 챕터는 본문 추출과
      sub-agent 디스패치, 렌더링 모두에서 제외됩니다. **찾아보기·색인·
      판권·저자 소개 같은 비본문 페이지가 목차 후보에 섞여 들어왔을 때
      사용**하세요.
    - book_info: 메인 LLM이 scanned_text(또는 OCR 모드의 scan_page_images)와
      PDF 메타로 보강한 책 정보
    - language: "ko" | "en". **OCR 모드에서는 텍스트 언어 감지가 불가능하므로,
      scan_page_images를 읽고 파악한 본문 언어를 반드시 전달**하세요. (text
      모드는 scan_pdf가 자동 감지하므로 생략 가능)

    OCR 모드(extraction_mode="ocr")에서는 본문 텍스트를 추출하지 않습니다.
    서브에이전트가 get_chapter_content가 렌더한 페이지 이미지를 직접 읽습니다.
    다음 단계: get_subagent_prompts(work_id)
    """
    data = analysis.set_chapters_impl(work_id, chapters, book_info, language=language)
    n_skip = sum(1 for c in data["chapters"] if c.get("skipped"))
    n_body = data["chapter_count"] - n_skip
    return _ok(data, next_action=(
        f"본문 챕터 {n_body}개 등록"
        + (f"({n_skip}개는 skip=비본문)" if n_skip else "")
        + f". 다음: get_subagent_prompts(work_id=\"{work_id}\")로 요약/문제 "
        "프롬프트와 chapter_ids·workflow를 받으세요. 이후 챕터 처리는 반드시 "
        "**등록된 chapter_id(ch1·ch2…)** 로만 get_chapter_content를 호출하세요 — "
        "'p11-p18' 같은 페이지 범위 문자열을 chapter_id로 쓰지 마세요(특정 페이지를 "
        "보려면 scan_page_images 경로를 직접 여세요)."
    ))


# ---------------------------------------------------------------------------
# 4. get_chapter_content
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("get_chapter_content")
def get_chapter_content(work_id: str, chapter_id: str) -> dict[str, Any]:
    """챕터 본문을 반환합니다 (extraction_mode에 따라 형태가 다름).

    - text 모드: `text`(본문) + `image_refs`(그림 절대경로)를 반환. sub-agent는
      text를 읽고, 필요 시 image_refs를 멀티모달 입력으로 로드하세요.
    - ocr 모드: 본문 텍스트가 없습니다. 대신 `page_images`(이 챕터 페이지들을
      렌더한 JPEG 절대경로)를 반환합니다. **sub-agent는 page_images를 순서대로
      멀티모달 입력으로 읽어 본문을 직접 파악(OCR)**한 뒤, 읽어낸 글자수로
      문제 개수·요약 길이 스케일을 정하세요. 흐릿한 기술용어·식별자·예약어는
      문맥으로 복원하세요. `image_refs`(그림)는 비어 있을 수 있습니다.
    """
    raw = analysis.get_chapter_content_impl(work_id, chapter_id)
    if "page_images" in raw:  # ocr 모드
        guide = (
            f"이 챕터({chapter_id})의 page_images를 **순서대로** 멀티모달로 읽어 "
            "본문을 직접 파악(OCR)하세요. 읽어낸 글자수로 문제 개수·요약 길이 "
            "스케일을 정하고, summarizer_prompt 스키마대로 결과를 만들어 "
        )
    else:  # text 모드
        guide = (
            f"이 챕터({chapter_id})의 text(+필요 시 image_refs)를 읽고 "
            "summarizer_prompt 스키마대로 요약·문제를 만들어 "
        )
    return _ok(raw, next_action=(
        guide + f"save_chapter_result(work_id=\"{work_id}\", "
        f"chapter_id=\"{chapter_id}\", data=...)로 저장하세요. extension이 "
        "활성이면 같은 챕터에 대해 search_extension_context→extension→"
        "save_extension_result도 처리한 뒤, 다음 챕터로 넘어가세요."
    ))


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
    ext_on = data["enabled_types"]["extension"]
    return _ok(data, next_action=(
        f"workflow_instructions를 따라 chapter_ids({data['chapter_ids']})를 "
        "순회하세요. 각 챕터: get_chapter_content(그 chapter_id) → summarizer_prompt로 "
        "요약/문제 생성 → save_chapter_result"
        + ("(+ extension 활성: search_extension_context→save_extension_result)" if ext_on else "")
        + ". chapter_id는 반드시 위 목록의 값(ch1·ch2…)을 쓰고, 페이지 범위 문자열은 "
        "쓰지 마세요. mode가 'ocr'이면 본문 대신 page_images를 직접 읽습니다."
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
    return _ok({"saved_path": str(path)}, next_action=(
        f"{chapter_id} 요약/문제 저장 완료. extension이 활성이면 이 챕터의 "
        "확장 문제도(search_extension_context→save_extension_result) 처리하세요. "
        "그다음 남은 chapter_id로 진행하고, 전부 끝나면 "
        f"list_pending_chapters(work_id=\"{work_id}\")로 누락이 없는지 확인 후 "
        "finalize_study를 호출하세요."
    ))


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
    return _ok({"saved_path": str(path)}, next_action=(
        f"{chapter_id} 확장 문제 저장 완료. 남은 chapter_id로 진행하세요. "
        f"모두 끝나면 list_pending_chapters(work_id=\"{work_id}\")로 확인 후 "
        "finalize_study(output_format=…)로 마무리하세요."
    ))


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
    }, next_action=(
        "results(외부 자료)로 챕터와 연결된 확장 문제를 만들어 "
        f"save_extension_result(work_id=\"{work_id}\", chapter_id=\"{chapter_id}\", "
        "data=...)로 저장하세요. results가 비었으면 본문 지식만으로 만들고 "
        "각 문제의 sources는 빈 배열로 두세요."
    ))


# ---------------------------------------------------------------------------
# 9. get_work_state
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
    summary_pending = pending["summary_pending"]
    ext_pending = pending["extension_pending"] if opts.get("extension") else []
    blocking = sorted(set(summary_pending) | set(ext_pending))
    if blocking:
        na = (
            f"아직 처리할 챕터: summary={summary_pending}, extension={ext_pending}. "
            "각 챕터를 get_chapter_content→(요약/문제)→save_chapter_result"
            "(+save_extension_result)로 끝내세요. 이미 한 번 실패한 챕터는 1회만 "
            "재시도하고, 계속 실패하면 finalize_study(force=True)로 부분 렌더가 "
            "가능합니다."
        )
    else:
        na = (
            "모든 챕터 처리 완료. finalize_study(work_id, output_format)로 "
            "마무리하세요 — output_format은 사용자에게 'html(웹 사이트) / "
            "md_tui(터미널 학습)' 중 물어보고 그 선택을 전달하세요."
        )
    return _ok({
        "summary_pending": summary_pending,
        "extension_pending": ext_pending,
        "extension_enabled": bool(opts.get("extension")),
    }, next_action=na)


# ---------------------------------------------------------------------------
# 11. finalize_study
# ---------------------------------------------------------------------------

@mcp.tool()
@_safe("finalize_study")
def finalize_study(
    work_id: str,
    output_format: str = "",
    keep_work_dir: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """학습 자료를 output_dir에 렌더링합니다.

    - output_format: "html" | "md_tui". **기본값 없음 — 임의로 정하지 말고
      반드시 사용자에게 물어 선택을 받으세요.** 미지정 시 거부됩니다.
        - html: 정적 사이트 (브라우저로 열람)
        - md_tui: 챕터별 폴더 + summary.md + 학습 TUI
    - keep_work_dir: False면 .work/ 폴더 삭제
    - force: 아직 처리되지 않은 챕터가 남아 있어도 강제로 렌더링.
      기본값 False면 pending 챕터가 있을 때 거부하고 목록을 돌려줍니다
      (조용한 부분 렌더링 방지). 일부 챕터가 끝내 실패해 부분 결과라도
      만들고 싶을 때만 force=True를 사용하세요.

    응답의 next_action에 학습 자료 실행 명령이 포함됩니다.
    - html: `python3 study_html.py`(진도 API 서버). 이 서버를 띄워야 답안/완료
      토글이 progress/ 폴더에 저장되며, 파일을 직접 열면(file://) 동작 안 함.
    - md_tui: `python3 study_tui.py`(터미널 TUI). rich가 없으면 자동 설치 시도,
      불가능한 환경이면 평문 모드로 폴백(항상 실행). 진도는 각 챕터
      progress.json에 직접 저장(서버 불필요).
    """
    if not output_format:
        return _err(
            "output_format이 지정되지 않았습니다. 기본값을 임의로 정하지 말고, "
            "사용자에게 'html: 정적 웹사이트로 열람 / "
            "md_tui: 챕터별 Markdown + 학습 TUI' 중 무엇을 원하는지 물어본 뒤 "
            "그 선택을 output_format으로 전달해 다시 호출하세요.",
            data={"choices": list(RENDERERS)},
        )
    renderer_cls = RENDERERS.get(output_format)
    if renderer_cls is None:
        return _err(
            f"unknown output_format: {output_format!r}. "
            f"choices={list(RENDERERS)}"
        )

    state = workspace.load_state(work_id)

    # 완료 가드: pending 챕터가 남아 있으면 거부 (force로 우회 가능)
    pending = workspace.list_pending_chapters_impl(work_id)
    opts = state.get("question_options", {})
    ext_pending = pending["extension_pending"] if opts.get("extension") else []
    blocking = sorted(set(pending["summary_pending"]) | set(ext_pending))
    if blocking and not force:
        return _err(
            f"아직 처리되지 않은 챕터가 있습니다: {blocking}. "
            "먼저 완료하거나, 부분 결과로 강제 렌더링하려면 force=True로 "
            "호출하세요.",
            data={
                "summary_pending": pending["summary_pending"],
                "extension_pending": ext_pending,
            },
        )

    output_dir = Path(state["output_dir"])

    renderer = renderer_cls()
    renderer.render(work_id, output_dir)  # NotImplementedError 시 _safe가 잡음

    workspace.update_phase(work_id, "rendering", "completed")

    if not keep_work_dir:
        work_dir = workspace.get_work_dir(work_id)
        if work_dir.exists():
            shutil.rmtree(work_dir)

    # 중간 데이터(.work) 정리 안내 — 두 포맷 공통
    work_cleanup = (
        "\n\n[작업 데이터 정리] 중간 작업 폴더(.work/: 페이지 이미지·raw·상태 파일)가 "
        "보존되어 있습니다"
        + (" (현재 keep_work_dir=False라 이미 삭제됨)." if not keep_work_dir
           else f" ({workspace.get_work_dir(work_id)}).")
        + f" 사용자에게 이 중간 데이터를 삭제할지 보존할지 물어보세요. 삭제를 원하면 "
        f"finalize_study(work_id=\"{work_id}\", output_format=\"{output_format}\", "
        "keep_work_dir=False)로 다시 호출하면 .work/가 제거됩니다(재실행 시 캐시로 "
        "쓰려면 보존)."
    )

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
            "work_dir_kept": keep_work_dir,
            "launch_command": launch_cmd,
            "entry_script": "study_tui.py",
            "python": py,
        }
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
            + work_cleanup
        )
        return _ok(data, next_action=next_action)

    # html — 정적 사이트 + 진도 API 서버(study_html.py). stdlib만 쓰므로 어떤
    # python으로도 동작하지만, 일관성을 위해 같은 인터프리터를 안내한다.
    launch_cmd = f"cd {output_dir} && {py} study_html.py"
    entry = "index.html" if (output_dir / "index.html").exists() else "main.html"
    return _ok(
        {
            "output_dir": str(output_dir),
            "format": output_format,
            "work_dir_kept": keep_work_dir,
            "launch_command": launch_cmd,
            "python": py,
            "entry_page": entry,
            "default_url": "http://localhost:8765/" + entry,
        },
        next_action=(
            f"학습 자료가 {output_dir}에 만들어졌습니다.\n"
            f"\n[서버 시작] 진도 저장과 완료 토글이 동작하려면 다음 명령을 "
            f"실행하세요:\n"
            f"  {launch_cmd}\n"
            f"기본 포트는 8765이며 브라우저가 자동으로 "
            f"http://localhost:8765/{entry} 를 엽니다. 파일을 더블클릭"
            f"(file://)으로 열면 /api/progress 호출이 막혀 답안이 저장되지 "
            f"않습니다.\n"
            f"\n[서버 종료] 서버를 실행한 터미널에서 Ctrl+C 를 누르세요. "
            f"브라우저 탭/창을 닫는 것만으로는 서버가 꺼지지 않습니다. "
            f"백그라운드로 띄웠다면 `lsof -i :8765` 로 PID를 찾아 `kill <pid>` "
            f"하거나, `pkill -f \"study_html.py --port 8765\"` 로 종료할 수 있습니다."
            + work_cleanup
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
