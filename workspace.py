"""워크스페이스(.work/) 관리 + state.json 동시성 보호.

폴더 레이아웃 (output_dir 안에 생성):
    .work/
    ├── state.json
    ├── raw_data/
    │   ├── outline.json
    │   ├── book_info.json
    │   ├── chapters_raw/ch{N}.json
    │   └── pages/p{N}.jpg          # 목차/본문 OCR 입력 이미지 캐시
    └── chapters/
        ├── summaries/ch{N}.json           # 요약 + 핵심포인트
        ├── quiz/ch{N}.json                # 기본 문제 (mc/sa/rf)
        └── extension_quiz/ch{N}.json # 확장 문제

work_id 컨벤션: YYYYMMDD-HHMMSS (단순 타임스탬프).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from . import question_contract

logger = logging.getLogger(__name__)

OUTPUT_MANIFEST_NAME = ".pdf-learner-manifest.json"

# work_id 별 in-memory lock (state.json read-modify-write 직렬화)
_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()

# 같은 work_id의 set_chapters setup+본문 준비 전체를 직렬화한다.
_chapter_setup_locks: dict[str, threading.Lock] = {}
_chapter_setup_locks_meta = threading.Lock()

# work_id → work_dir 매핑 (단일 프로세스 내에서만 유효)
_registry: dict[str, Path] = {}
_registry_meta = threading.Lock()


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _get_lock(work_id: str) -> threading.Lock:
    with _locks_meta:
        lock = _locks.get(work_id)
        if lock is None:
            lock = threading.Lock()
            _locks[work_id] = lock
        return lock


@contextmanager
def chapter_setup_session(work_id: str):
    """같은 작업의 setup commit과 본문 준비가 서로 겹치지 않게 한다."""
    with _chapter_setup_locks_meta:
        lock = _chapter_setup_locks.get(work_id)
        if lock is None:
            lock = threading.Lock()
            _chapter_setup_locks[work_id] = lock
    with lock:
        yield


def _atomic_write_json(path: Path, data: dict | list) -> None:
    """tempfile → os.replace로 부분 손상 방지하며 JSON 저장."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    )
    tmp_path = Path(tmp.name)
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(str(tmp_path), str(path))
    except Exception:
        tmp.close()
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def canonicalize_chapter_page_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """챕터 페이지 메타를 새 키로 정규화한다.

    기존 작업과 구형 클라이언트의 page_range/printed_range는 읽되, 반환값에는
    pdf_pages/source_pages만 남긴다. source_pages의 명시적 null도 보존한다.
    """
    normalized = dict(data)
    if "pdf_pages" not in normalized and "page_range" in normalized:
        normalized["pdf_pages"] = normalized["page_range"]
    if "source_pages" not in normalized and "printed_range" in normalized:
        normalized["source_pages"] = normalized["printed_range"]
    normalized.pop("page_range", None)
    normalized.pop("printed_range", None)
    return normalized


def _canonicalize_state_page_metadata(state: dict[str, Any]) -> dict[str, Any]:
    chapters = state.get("chapters")
    if not isinstance(chapters, dict):
        return state
    state = dict(state)
    state["chapters"] = {
        chapter_id: canonicalize_chapter_page_metadata(chapter)
        if isinstance(chapter, dict) else chapter
        for chapter_id, chapter in chapters.items()
    }
    return state


def make_work_id() -> str:
    """work_id 발급. 외부에서도 default output_dir 작명에 쓸 수 있게 공개."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


# 하위호환 alias
_make_work_id = make_work_id


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 레지스트리 / 경로 헬퍼
# ---------------------------------------------------------------------------

def register(work_id: str, work_dir: Path) -> None:
    with _registry_meta:
        _registry[work_id] = work_dir


def get_work_dir(work_id: str) -> Path:
    with _registry_meta:
        try:
            return _registry[work_id]
        except KeyError as e:
            raise KeyError(f"unknown work_id: {work_id}") from e


def state_path(work_id: str) -> Path:
    return get_work_dir(work_id) / "state.json"


def raw_data_dir(work_id: str) -> Path:
    return get_work_dir(work_id) / "raw_data"


def chapters_raw_dir(work_id: str) -> Path:
    return raw_data_dir(work_id) / "chapters_raw"


def pages_dir(work_id: str) -> Path:
    """목차/본문 OCR 입력으로 쓸 페이지 JPEG 캐시 위치 (p{N}.jpg)."""
    return raw_data_dir(work_id) / "pages"


def chapters_dir(work_id: str) -> Path:
    """챕터별 sub-agent 산출물의 부모 폴더 (summaries/quiz/extension_quiz)."""
    return get_work_dir(work_id) / "chapters"


def summaries_dir(work_id: str) -> Path:
    """요약 + 핵심포인트 (summary, key_points)."""
    return chapters_dir(work_id) / "summaries"


def quiz_dir(work_id: str) -> Path:
    """기본 문제 (multiple_choice / short_answer / reflection)."""
    return chapters_dir(work_id) / "quiz"


def extension_quiz_dir(work_id: str) -> Path:
    """확장 문제 (extension)."""
    return chapters_dir(work_id) / "extension_quiz"


def book_info_path(work_id: str) -> Path:
    return raw_data_dir(work_id) / "book_info.json"


def outline_path(work_id: str) -> Path:
    return raw_data_dir(work_id) / "outline.json"


def legacy_output_formats(output_dir: str | Path) -> set[str]:
    """manifest 도입 전 생성물의 형식을 보수적으로 식별한다."""
    out = Path(output_dir).resolve()
    if not out.is_dir():
        return set()
    formats: set[str] = set()
    html_entry = (out / "index.html").is_file() or (out / "main.html").is_file()
    if html_entry and (out / "study_html.py").is_file() and (out / "assets").is_dir():
        formats.add("html")
    md_chapter = any(
        path.is_dir()
        and (path / "summary.md").is_file()
        and (path / "quiz.json").is_file()
        and (path / "study_tui.py").is_file()
        for path in out.iterdir()
    )
    if (out / "book.md").is_file() and (out / "study_tui.py").is_file() and md_chapter:
        formats.add("md_tui")
    return formats


def inspect_output_dir(output_dir: str | Path) -> dict[str, Any]:
    """출력 폴더가 새 작업에 사용 가능한지 읽기 전용으로 확인한다."""
    out = Path(output_dir).resolve()
    state_file = out / ".work" / "state.json"
    manifest_file = out / OUTPUT_MANIFEST_NAME

    if state_file.exists():
        try:
            state = _read_json(state_file)
        except (OSError, ValueError, TypeError):
            return {
                "kind": "damaged_managed_work",
                "output_dir": str(out),
                "work_id": None,
                "pdf_path": None,
                "current_phase": None,
                "can_resume": False,
            }
        return {
            "kind": "managed_work",
            "output_dir": str(out),
            "work_id": state.get("work_id"),
            "pdf_path": state.get("pdf_path"),
            "current_phase": state.get("current_phase"),
            "can_resume": bool(state.get("work_id")),
        }

    if manifest_file.exists() or legacy_output_formats(out):
        return {
            "kind": "managed_output",
            "output_dir": str(out),
            "work_id": None,
            "pdf_path": None,
            "current_phase": "rendered",
            "can_resume": False,
        }

    if out.exists() and any(out.iterdir()):
        return {
            "kind": "unmanaged_content",
            "output_dir": str(out),
            "work_id": None,
            "pdf_path": None,
            "current_phase": None,
            "can_resume": False,
        }

    return {
        "kind": "available",
        "output_dir": str(out),
        "work_id": None,
        "pdf_path": None,
        "current_phase": None,
        "can_resume": False,
    }


def replace_workspace(output_dir: str | Path) -> None:
    """명시적 교체 요청에 따라 기존 `.work`만 제거한다.

    이전 렌더 결과와 manifest는 새 렌더가 성공할 때까지 보존한다.
    """
    out = Path(output_dir).resolve()
    work_dir = out / ".work"
    if work_dir.parent != out or work_dir.name != ".work":
        raise ValueError(f"unsafe work directory: {work_dir}")
    if not work_dir.exists():
        return

    state_file = work_dir / "state.json"
    old_work_id: str | None = None
    if state_file.exists():
        try:
            old_work_id = _read_json(state_file).get("work_id")
        except (OSError, ValueError, TypeError):
            old_work_id = None

    lock = _get_lock(old_work_id) if old_work_id else threading.Lock()
    with lock:
        shutil.rmtree(work_dir)

    if old_work_id:
        with _registry_meta:
            _registry.pop(old_work_id, None)
        with _locks_meta:
            _locks.pop(old_work_id, None)
        with _chapter_setup_locks_meta:
            _chapter_setup_locks.pop(old_work_id, None)


def cleanup_workspace(work_id: str) -> dict[str, str | bool]:
    """완료된 작업의 `.work`만 안전하게 삭제하고 메모리 등록을 해제한다."""
    work_dir = get_work_dir(work_id)
    output_dir = work_dir.parent
    if work_dir.name != ".work":
        raise ValueError(f"unsafe work directory: {work_dir}")

    with _get_lock(work_id):
        state = load_state(work_id)
        if state.get("phases", {}).get("rendering") != "completed":
            raise ValueError(
                "cleanup_work requires completed rendering; run finalize_study first"
            )
        if not work_dir.exists():
            raise FileNotFoundError(f"work directory not found: {work_dir}")
        shutil.rmtree(work_dir)

    with _registry_meta:
        _registry.pop(work_id, None)
    with _locks_meta:
        _locks.pop(work_id, None)
    with _chapter_setup_locks_meta:
        _chapter_setup_locks.pop(work_id, None)

    return {
        "work_id": work_id,
        "output_dir": str(output_dir),
        "work_dir_deleted": True,
    }


# ---------------------------------------------------------------------------
# 워크스페이스 생성
# ---------------------------------------------------------------------------

VALID_EXECUTION_MODES = ("sequential", "parallel")
VALID_EXTRACTION_MODES = ("text", "ocr")


def _validate_options(
    options: dict[str, bool | None],
) -> dict[str, bool | None]:
    """문제 유형 설정을 검증한다.

    객관식만 기존 기본 활성 상태를 유지한다. 단답형·주관식·확장형의 ``None``은
    init_work 뒤 사용자 선택을 기다리는 상태이며 scan_pdf에서 확정된다.
    """
    defaults: dict[str, bool | None] = {
        "multiple_choice": True,
        "short_answer": None,
        "reflection": None,
        "extension": None,
    }
    normalized: dict[str, bool | None] = {}
    for key, default in defaults.items():
        value = options.get(key, default)
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{key} must be true, false, or null")
        normalized[key] = value

    if all(value is False for value in normalized.values()):
        raise ValueError("at least one question type must be enabled")
    return normalized


def validate_workspace_inputs(
    pdf_path: str | os.PathLike,
    options: dict[str, bool | None],
    user_context: str = "",
    execution_mode: str | None = None,
    extraction_mode: str | None = None,
) -> dict[str, bool | None]:
    """워크스페이스 생성 입력을 파일 변경 없이 검증한다."""
    pdf = Path(pdf_path)
    if not pdf.exists():
        raise ValueError(f"PDF not found: {pdf_path}")
    if execution_mode is not None and execution_mode not in VALID_EXECUTION_MODES:
        raise ValueError(
            f"execution_mode must be one of {VALID_EXECUTION_MODES}, got {execution_mode!r}"
        )
    if extraction_mode is not None and extraction_mode not in VALID_EXTRACTION_MODES:
        raise ValueError(
            f"extraction_mode must be one of {VALID_EXTRACTION_MODES}, got {extraction_mode!r}"
        )
    if not isinstance(user_context, str):
        raise ValueError("user_context must be a string")
    return _validate_options(options)


def create_workspace(
    pdf_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    options: dict[str, bool | None],
    user_context: str = "",
    user_context_confirmed: bool = False,
    execution_mode: str | None = None,
    extraction_mode: str | None = None,
    work_id: str | None = None,
) -> str:
    """워크스페이스 생성 → work_id 반환.

    output_dir/.work/ 아래에 state.json과 하위 폴더들을 초기화한다.
    이미 .work/state.json이 있으면 새 work_id로 덮어쓴다 (재초기화).
    work_id를 인자로 받으면(외부에서 미리 발급) 그 값으로 등록. 없으면 새로 발급.

    execution_mode·extraction_mode는 **set_chapters에서 확정**하므로 init 시점에는
    보통 None(미정)이다. 값을 주면 검증 후 기록한다.

    Raises:
        ValueError: pdf_path 미존재, 주어진 모드가 잘못됨, 모든 문제 비활성.
    """
    pdf = Path(pdf_path)
    question_options = validate_workspace_inputs(
        pdf,
        options,
        user_context,
        execution_mode,
        extraction_mode,
    )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    work_dir = out / ".work"

    # 하위 디렉터리 미리 생성
    (work_dir / "raw_data" / "chapters_raw").mkdir(parents=True, exist_ok=True)
    (work_dir / "raw_data" / "pages").mkdir(parents=True, exist_ok=True)
    (work_dir / "chapters" / "summaries").mkdir(parents=True, exist_ok=True)
    (work_dir / "chapters" / "quiz").mkdir(parents=True, exist_ok=True)
    (work_dir / "chapters" / "extension_quiz").mkdir(parents=True, exist_ok=True)

    if work_id is None:
        work_id = make_work_id()
    register(work_id, work_dir)

    initial_state: dict[str, Any] = {
        "work_id": work_id,
        "pdf_path": str(pdf.resolve()),
        "output_dir": str(out.resolve()),
        "started_at": _now_iso(),
        "execution_mode": execution_mode,
        "extraction_mode": extraction_mode,
        "question_options": question_options,
        "user_context": user_context.strip(),
        "user_context_confirmed": (
            user_context_confirmed or bool(user_context.strip())
        ),
        "page_count": None,
        "text_quality": None,
        "ocr_language": None,
        # current_phase 문자열만 소비된다(get_work_state 응답·에러 메시지). phases는
        # update_phase가 갱신하는 진행 텔레메트리이며 분기 로직엔 쓰지 않는다.
        "current_phase": "init",
        "phases": {
            "scanning": "pending",
            "chapter_setup": "pending",
            "chapter_processing": "pending",
            "rendering": "pending",
        },
        "chapters": {},
    }
    save_state(work_id, initial_state)
    logger.info("workspace created: work_id=%s, work_dir=%s", work_id, work_dir)
    return work_id


# ---------------------------------------------------------------------------
# state.json read / write
# ---------------------------------------------------------------------------

def load_state(work_id: str) -> dict[str, Any]:
    """state.json 읽기. lock 안 잡음 (read-only).

    호출자가 read-modify-write를 한다면 직접 _get_lock(work_id) 사용 권장.
    이 모듈의 update_* 헬퍼는 내부에서 lock을 잡으므로 그쪽을 우선 사용.
    """
    return _canonicalize_state_page_metadata(_read_json(state_path(work_id)))


def save_state(work_id: str, state: dict[str, Any]) -> None:
    """state.json 덮어쓰기 (atomic). lock은 호출자 책임."""
    _atomic_write_json(state_path(work_id), state)


def resume_workspace(output_dir: str | Path) -> dict[str, Any]:
    """기존 <output_dir>/.work/state.json을 읽어 레지스트리를 재구성한다.

    _registry는 메모리에만 존재하므로 (create_workspace에서만 register됨)
    MCP 서버가 재시작되면 work_id → work_dir 매핑이 사라진다. 이 함수는
    디스크에 보존된 state.json에서 work_id를 복원해 register를 다시 호출,
    이후 모든 도구가 정상 동작하도록 한다.

    Returns:
        복원된 state dict (work_id 포함).

    Raises:
        FileNotFoundError: 해당 output_dir에 .work/state.json이 없음.
        ValueError: state.json에 work_id가 없음 (손상).
    """
    work_dir = Path(output_dir).resolve() / ".work"
    sp = work_dir / "state.json"
    if not sp.exists():
        raise FileNotFoundError(f"재개할 작업이 없습니다: {sp}")
    state = _canonicalize_state_page_metadata(_read_json(sp))
    work_id = state.get("work_id")
    if not work_id:
        raise ValueError(f"state.json에 work_id가 없습니다 (손상 가능): {sp}")
    register(work_id, work_dir)
    logger.info("workspace resumed: work_id=%s, work_dir=%s", work_id, work_dir)
    return state


def update_state(work_id: str, **top_level_updates: Any) -> dict[str, Any]:
    """state.json의 top-level 필드를 patch (lock 보호 + atomic)."""
    with _get_lock(work_id):
        state = load_state(work_id)
        state.update(top_level_updates)
        save_state(work_id, state)
        return state


def confirm_question_setup(
    work_id: str,
    *,
    enable_short_answer: bool | None = None,
    enable_reflection: bool | None = None,
    enable_extension: bool | None = None,
    user_context: str | None = None,
) -> dict[str, Any]:
    """init_work에서 미정인 문제 유형을 한 번만 확정한다.

    모든 검증은 같은 잠금 구간에서 끝낸 뒤 한 번에 저장한다. 이미 확정된 선택을
    scan_pdf 재호출이 조용히 바꾸는 것도 거부한다.
    """
    supplied = {
        "short_answer": enable_short_answer,
        "reflection": enable_reflection,
        "extension": enable_extension,
    }
    with _get_lock(work_id):
        state = load_state(work_id)
        options = dict(state.get("question_options") or {})

        for key, value in supplied.items():
            current = options.get(key)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"enable_{key} must be true or false")
            if current is None:
                if value is None:
                    raise ValueError(f"enable_{key} selection is required")
                options[key] = value
            elif value is not None and value != current:
                raise ValueError(f"enable_{key} is already confirmed as {current}")

        if any(options.get(key) is None for key in (
            "short_answer", "reflection", "extension",
        )):
            raise ValueError("all pending question type selections are required")
        if not any(options.values()):
            raise ValueError("at least one question type must be enabled")

        if user_context is not None:
            if not isinstance(user_context, str):
                raise ValueError("user_context must be a string")
            normalized_context = user_context.strip()
            current_context = state.get("user_context", "") or ""
            if current_context and normalized_context != current_context:
                raise ValueError("user_context is already confirmed")
            state["user_context"] = normalized_context
        state["user_context_confirmed"] = True

        state["question_options"] = options
        save_state(work_id, state)
        return state


def update_phase(work_id: str, phase: str, status: str) -> None:
    """phases[phase] = status. current_phase도 함께 갱신."""
    with _get_lock(work_id):
        state = load_state(work_id)
        if phase not in state["phases"]:
            raise KeyError(f"unknown phase: {phase}")
        state["phases"][phase] = status
        state["current_phase"] = phase
        save_state(work_id, state)


def update_chapter_status(work_id: str, chapter_id: str, **updates: Any) -> None:
    """chapters[chapter_id]에 임의 필드 patch (lock 보호 + atomic).

    예: update_chapter_status(work_id, "ch1", summary_status="completed", error=None)
    """
    with _get_lock(work_id):
        state = load_state(work_id)
        chapters = state.setdefault("chapters", {})
        entry = chapters.get(chapter_id)
        if entry is None:
            raise KeyError(f"chapter not in state: {chapter_id}")
        entry.update(updates)
        save_state(work_id, state)


def _build_chapter_state(
    chapters: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """검증·정규화된 챕터 정의를 초기 state 항목으로 변환한다."""
    new_chapters: dict[str, dict[str, Any]] = {}
    for raw_chapter in chapters:
        ch = canonicalize_chapter_page_metadata(raw_chapter)
        cid = ch["chapter_id"]
        skip = bool(ch.get("skip", False))
        status = "skipped" if skip else "pending"
        new_chapters[cid] = {
            "title": ch["title"],
            "pdf_pages": list(ch["pdf_pages"]),
            "char_count": ch.get("char_count", 0),
            "skip": skip,
            "summary_status": status,
            "extension_status": status,
            "error": None,
            "retry_count": 0,
        }
        if "source_pages" in ch:
            source_pages = ch["source_pages"]
            new_chapters[cid]["source_pages"] = (
                list(source_pages) if source_pages is not None else None
            )
    return new_chapters


def set_chapters_in_state(work_id: str, chapters: list[dict[str, Any]]) -> None:
    """set_chapters 도구가 결정한 챕터 구조를 state.chapters에 반영.

    각 chapter는 최소 {chapter_id, title, pdf_pages} 보유.
    optional "skip": True → 색인/판권/찾아보기 같은 비본문 챕터.
    이 경우 summary/extension status가 "skipped"로 초기화되고
    raw 추출·sub-agent 디스패치·렌더링 모두에서 제외된다.
    """
    with _get_lock(work_id):
        state = load_state(work_id)
        state["chapters"] = _build_chapter_state(chapters)
        state["phases"]["chapter_setup"] = "completed"
        save_state(work_id, state)


def commit_chapter_setup(
    work_id: str,
    chapters: list[dict[str, Any]],
    *,
    execution_mode: str,
    extraction_mode: str,
    ocr_language: str | None = None,
    book_info: dict[str, Any],
) -> dict[str, Any]:
    """검증이 끝난 챕터 설정과 처리 시작 상태를 한 잠금 구간에서 확정한다.

    book_info는 state보다 먼저 atomic write하고, state 저장이 실패하면 호출 전
    바이트로 복원한다. 따라서 실패한 setup commit이 메타 파일만 바꾸지 않는다.
    """
    with _get_lock(work_id):
        state = load_state(work_id)
        info_path = book_info_path(work_id)
        snapshot = _snapshot_files([info_path])
        try:
            _atomic_write_json(info_path, book_info)
            state["execution_mode"] = execution_mode
            state["extraction_mode"] = extraction_mode
            if extraction_mode == "ocr":
                state["ocr_language"] = ocr_language
            state["chapters"] = _build_chapter_state(chapters)
            state["phases"]["chapter_setup"] = "completed"
            state["phases"]["chapter_processing"] = "in_progress"
            state["current_phase"] = "chapter_processing"
            save_state(work_id, state)
        except Exception:
            try:
                _restore_files(snapshot, raise_on_error=True)
            except Exception as rollback_error:
                raise RuntimeError(
                    "chapter setup failed and book_info rollback failed"
                ) from rollback_error
            raise
        return state


# ---------------------------------------------------------------------------
# book_info / outline / chapter raw I/O
# ---------------------------------------------------------------------------

def save_book_info(work_id: str, book_info: dict[str, Any]) -> Path:
    path = book_info_path(work_id)
    _atomic_write_json(path, book_info)
    return path


def load_book_info(work_id: str) -> dict[str, Any] | None:
    path = book_info_path(work_id)
    if not path.exists():
        return None
    return _read_json(path)


def save_outline(work_id: str, outline: dict[str, Any]) -> Path:
    path = outline_path(work_id)
    _atomic_write_json(path, outline)
    return path


def load_outline(work_id: str) -> dict[str, Any] | None:
    path = outline_path(work_id)
    if not path.exists():
        return None
    return _read_json(path)


def save_chapter_raw(work_id: str, chapter_id: str, data: dict[str, Any]) -> Path:
    """PDF 처리 결과(raw 본문 text + char_count)를 chapters_raw/에 저장."""
    out = chapters_raw_dir(work_id) / f"{chapter_id}.json"
    _atomic_write_json(out, canonicalize_chapter_page_metadata(data))
    return out


def get_chapter_raw(work_id: str, chapter_id: str) -> dict[str, Any]:
    """챕터 raw 데이터 읽기 (없으면 FileNotFoundError)."""
    path = chapters_raw_dir(work_id) / f"{chapter_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"chapter raw not found: {chapter_id}")
    return canonicalize_chapter_page_metadata(_read_json(path))


# ---------------------------------------------------------------------------
# sub-agent 결과 저장 — 동시성 안전
# ---------------------------------------------------------------------------

def _require_result_target(state: dict[str, Any], chapter_id: str) -> dict[str, Any]:
    chapters = state.get("chapters", {})
    entry = chapters.get(chapter_id)
    if entry is None:
        raise KeyError(f"chapter not in state: {chapter_id}")
    if entry.get("skip"):
        raise ValueError(f"chapter is skipped: {chapter_id}")
    return entry


def _snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def _restore_files(
    snapshot: dict[Path, bytes | None],
    *,
    raise_on_error: bool = False,
) -> None:
    for path, content in snapshot.items():
        try:
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=str(path.parent),
                    delete=False,
                    suffix=".tmp",
                )
                tmp_path = Path(tmp.name)
                try:
                    tmp.write(content)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                    tmp.close()
                    os.replace(str(tmp_path), str(path))
                except Exception:
                    tmp.close()
                    if tmp_path.exists():
                        tmp_path.unlink()
                    raise
        except OSError:
            if raise_on_error:
                raise
            logger.warning("failed to restore result file after state save error: %s", path)


def _saved_question_ids(path: Path) -> set[str]:
    """같은 챕터에서 먼저 저장된 다른 결과의 문제 ID를 읽는다."""
    if not path.exists():
        return set()
    data = _read_json(path)
    return question_contract.question_ids(data.get("questions"))


def get_chapter_summary(work_id: str, chapter_id: str) -> dict[str, Any]:
    """저장된 챕터 요약과 핵심 포인트를 반환한다."""
    path = summaries_dir(work_id) / f"{chapter_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"chapter summary not found: {chapter_id}")
    return _read_json(path)


def save_chapter_result(
    work_id: str,
    chapter_id: str,
    data: dict[str, Any],
) -> Path:
    """summarizer sub-agent의 챕터 결과를 분리 저장 + state 갱신.

    한 payload({summary, key_points, questions})를 두 파일로 나눠 쓴다:
      - summaries/ch{N}.json : questions·body_text를 제외한 요약 정보(title/summary/key_points)
      - quiz/ch{N}.json      : 기본 문제({chapter_id, questions})
    (둘은 항상 같은 호출에서 함께 생성된다 — 결합 유지)

    `body_text`가 들어오더라도 요약 저장에서 제외하며, set_chapters가 만든
    canonical raw text/char_count는 여기서 갱신하지 않는다.

    동시성: state.json 갱신은 _get_lock으로 직렬화. 파일 자체 쓰기는
    atomic rename이라 챕터 파일끼리도 충돌 없음.
    """
    summary_part = {
        k: v for k, v in data.items() if k not in ("questions", "body_text")
    }
    quiz_part = {
        "chapter_id": data.get("chapter_id", chapter_id),
        "questions": data.get("questions") or {},
    }
    out = summaries_dir(work_id) / f"{chapter_id}.json"
    quiz_out = quiz_dir(work_id) / f"{chapter_id}.json"

    with _get_lock(work_id):
        state = load_state(work_id)
        entry = _require_result_target(state, chapter_id)
        duplicate_ids = question_contract.invalid_question_id_paths(
            data.get("questions"),
            question_contract.BASIC_QUESTION_TYPES,
            existing_ids=_saved_question_ids(extension_quiz_dir(work_id) / f"{chapter_id}.json"),
        )
        if duplicate_ids:
            raise question_contract.QuestionContractError(duplicate_ids)
        snapshot = _snapshot_files([out, quiz_out])
        try:
            _atomic_write_json(out, summary_part)
            _atomic_write_json(quiz_out, quiz_part)
            entry["summary_status"] = "completed"
            entry["error"] = None
            save_state(work_id, state)
        except Exception:
            _restore_files(snapshot)
            raise

    return out


def save_extension_result(
    work_id: str,
    chapter_id: str,
    data: dict[str, Any],
) -> Path:
    """extension sub-agent 결과 저장 + state 갱신."""
    out = extension_quiz_dir(work_id) / f"{chapter_id}.json"

    with _get_lock(work_id):
        state = load_state(work_id)
        entry = _require_result_target(state, chapter_id)
        duplicate_ids = question_contract.invalid_question_id_paths(
            data.get("questions"),
            ("extension",),
            existing_ids=_saved_question_ids(quiz_dir(work_id) / f"{chapter_id}.json"),
        )
        if duplicate_ids:
            raise question_contract.QuestionContractError(duplicate_ids)
        snapshot = _snapshot_files([out])
        try:
            _atomic_write_json(out, data)
            entry["extension_status"] = "completed"
            save_state(work_id, state)
        except Exception:
            _restore_files(snapshot)
            raise

    return out


def mark_chapter_failed(
    work_id: str,
    chapter_id: str,
    *,
    kind: str,
    error: str,
) -> None:
    """sub-agent 실패 기록. kind는 'summary' | 'extension'."""
    if kind not in ("summary", "extension"):
        raise ValueError(f"kind must be 'summary' or 'extension', got {kind!r}")
    field = f"{kind}_status"
    with _get_lock(work_id):
        state = load_state(work_id)
        entry = state["chapters"].get(chapter_id)
        if entry is None:
            raise KeyError(f"chapter not in state: {chapter_id}")
        entry[field] = "failed"
        entry["error"] = error
        entry["retry_count"] = int(entry.get("retry_count", 0)) + 1
        save_state(work_id, state)


def mark_chapter_in_progress(work_id: str, chapter_id: str, *, kind: str) -> None:
    """'처리 시작'을 표시. kind는 'summary' | 'extension'.

    진행 모니터링용 soft 신호다. 이미 끝난 completed/skipped는 건드리지 않고,
    state에 없는 chapter_id면 조용히 무시한다(모니터링 표시 때문에 실제 작업을
    깨뜨리지 않기 위함). pending·failed·in_progress → in_progress.
    """
    if kind not in ("summary", "extension"):
        raise ValueError(f"kind must be 'summary' or 'extension', got {kind!r}")
    field = f"{kind}_status"
    with _get_lock(work_id):
        state = load_state(work_id)
        entry = state.get("chapters", {}).get(chapter_id)
        if entry is None:
            return
        if entry.get(field) not in _DONE_STATUSES:
            entry[field] = "in_progress"
            save_state(work_id, state)


# ---------------------------------------------------------------------------
# 조회 헬퍼
# ---------------------------------------------------------------------------

_DONE_STATUSES = ("completed", "skipped")


def _chapter_sort_key(chapter_id: str) -> tuple[int, str]:
    if chapter_id.startswith("ch") and chapter_id[2:].isdigit():
        return (int(chapter_id[2:]), chapter_id)
    return (10**9, chapter_id)


def pending_chapters_from_state(state: dict[str, Any]) -> dict[str, list[str]]:
    extension_enabled = bool(state.get("question_options", {}).get("extension"))
    summary_pending: list[str] = []
    extension_pending: list[str] = []
    for chapter_id in sorted(state.get("chapters", {}), key=_chapter_sort_key):
        entry = state["chapters"][chapter_id]
        if entry.get("skip"):
            continue
        if entry.get("summary_status") not in _DONE_STATUSES:
            summary_pending.append(chapter_id)
        if extension_enabled and entry.get("extension_status") not in _DONE_STATUSES:
            extension_pending.append(chapter_id)
    return {
        "summary_pending": summary_pending,
        "extension_pending": extension_pending,
    }


def list_pending_chapters_impl(work_id: str) -> dict[str, list[str]]:
    """summary/extension이 아직 처리되지 않은 챕터 ID 목록.

    `completed`와 `skipped`(비본문 챕터)는 처리 끝난 것으로 본다.

    Returns:
        {
            "summary_pending": ["ch1", "ch3"],
            "extension_pending": ["ch1", "ch2"],
        }
    """
    return pending_chapters_from_state(load_state(work_id))
