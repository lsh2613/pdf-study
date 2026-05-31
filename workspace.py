"""워크스페이스(.work/) 관리 + state.json 동시성 보호.

폴더 레이아웃 (output_dir 안에 생성):
    .work/
    ├── state.json
    ├── pdf_analysis/
    │   ├── outline.json
    │   ├── book_info.json
    │   ├── chapters_raw/ch{N}.json
    │   └── images/*.png
    ├── chapters/ch{N}.json
    └── extensions/ch{N}.json

work_id 컨벤션: YYYYMMDD-HHMMSS (단순 타임스탬프).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# work_id 별 in-memory lock (state.json read-modify-write 직렬화)
_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()

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


def _make_work_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


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


def pdf_analysis_dir(work_id: str) -> Path:
    return get_work_dir(work_id) / "pdf_analysis"


def chapters_raw_dir(work_id: str) -> Path:
    return pdf_analysis_dir(work_id) / "chapters_raw"


def images_dir(work_id: str) -> Path:
    return pdf_analysis_dir(work_id) / "images"


def chapters_dir(work_id: str) -> Path:
    return get_work_dir(work_id) / "chapters"


def extensions_dir(work_id: str) -> Path:
    return get_work_dir(work_id) / "extensions"


def book_info_path(work_id: str) -> Path:
    return pdf_analysis_dir(work_id) / "book_info.json"


def outline_path(work_id: str) -> Path:
    return pdf_analysis_dir(work_id) / "outline.json"


# ---------------------------------------------------------------------------
# 워크스페이스 생성
# ---------------------------------------------------------------------------

VALID_EXECUTION_MODES = ("sequential", "parallel")


def _validate_options(options: dict[str, bool]) -> dict[str, bool]:
    keys = ("multiple_choice", "short_answer", "reflection", "extension")
    normalized = {k: bool(options.get(k, True)) for k in keys}
    if not any(normalized.values()):
        raise ValueError("at least one question type must be enabled")
    return normalized


def create_workspace(
    pdf_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    options: dict[str, bool],
    user_context: str = "",
    execution_mode: str = "sequential",
) -> str:
    """워크스페이스 생성 → work_id 반환.

    output_dir/.work/ 아래에 state.json과 하위 폴더들을 초기화한다.
    이미 .work/state.json이 있으면 새 work_id로 덮어쓴다 (재초기화).

    Raises:
        ValueError: pdf_path 미존재, execution_mode 잘못됨, 모든 문제 비활성.
    """
    pdf = Path(pdf_path)
    if not pdf.exists():
        raise ValueError(f"PDF not found: {pdf_path}")

    if execution_mode not in VALID_EXECUTION_MODES:
        raise ValueError(
            f"execution_mode must be one of {VALID_EXECUTION_MODES}, got {execution_mode!r}"
        )

    question_options = _validate_options(options)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    work_dir = out / ".work"

    # 하위 디렉터리 미리 생성
    (work_dir / "pdf_analysis" / "chapters_raw").mkdir(parents=True, exist_ok=True)
    (work_dir / "pdf_analysis" / "images").mkdir(parents=True, exist_ok=True)
    (work_dir / "chapters").mkdir(parents=True, exist_ok=True)
    (work_dir / "extensions").mkdir(parents=True, exist_ok=True)

    work_id = _make_work_id()
    register(work_id, work_dir)

    initial_state: dict[str, Any] = {
        "work_id": work_id,
        "pdf_path": str(pdf.resolve()),
        "output_dir": str(out.resolve()),
        "started_at": _now_iso(),
        "execution_mode": execution_mode,
        "language": None,
        "question_options": question_options,
        "user_context": user_context or "",
        "page_count": None,
        "text_quality": None,
        "current_phase": "init",
        "phases": {
            "scanning": "pending",
            "chapter_setup": "pending",
            "chapter_processing": "pending",
            "extension_processing": "pending",
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
    return _read_json(state_path(work_id))


def save_state(work_id: str, state: dict[str, Any]) -> None:
    """state.json 덮어쓰기 (atomic). lock은 호출자 책임."""
    _atomic_write_json(state_path(work_id), state)


def update_state(work_id: str, **top_level_updates: Any) -> dict[str, Any]:
    """state.json의 top-level 필드를 patch (lock 보호 + atomic)."""
    with _get_lock(work_id):
        state = load_state(work_id)
        state.update(top_level_updates)
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


def set_chapters_in_state(work_id: str, chapters: list[dict[str, Any]]) -> None:
    """set_chapters 도구가 결정한 챕터 구조를 state.chapters에 반영.

    각 chapter는 최소 {chapter_id, title, page_range} 보유.
    optional "skip": True → 색인/판권/찾아보기 같은 비본문 챕터.
    이 경우 summary/extension status가 "skipped"로 초기화되고
    raw 추출·sub-agent 디스패치·렌더링 모두에서 제외된다.
    """
    with _get_lock(work_id):
        state = load_state(work_id)
        new_chapters: dict[str, dict[str, Any]] = {}
        for ch in chapters:
            cid = ch["chapter_id"]
            skip = bool(ch.get("skip", False))
            status = "skipped" if skip else "pending"
            new_chapters[cid] = {
                "title": ch["title"],
                "page_range": list(ch["page_range"]),
                "char_count": ch.get("char_count", 0),
                "skip": skip,
                "summary_status": status,
                "extension_status": status,
                "error": None,
                "retry_count": 0,
            }
        state["chapters"] = new_chapters
        state["phases"]["chapter_setup"] = "completed"
        save_state(work_id, state)


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
    """PDF 처리 결과(본문 + 이미지 참조)를 chapters_raw/에 저장."""
    out = chapters_raw_dir(work_id) / f"{chapter_id}.json"
    _atomic_write_json(out, data)
    return out


def get_chapter_raw(work_id: str, chapter_id: str) -> dict[str, Any]:
    """챕터 raw 데이터 읽기 (없으면 FileNotFoundError)."""
    path = chapters_raw_dir(work_id) / f"{chapter_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"chapter raw not found: {chapter_id}")
    return _read_json(path)


# ---------------------------------------------------------------------------
# sub-agent 결과 저장 — 동시성 안전
# ---------------------------------------------------------------------------

def save_chapter_result(
    work_id: str,
    chapter_id: str,
    data: dict[str, Any],
) -> Path:
    """summarizer sub-agent의 챕터 결과를 chapters/에 저장 + state 갱신.

    동시성: state.json 갱신은 _get_lock으로 직렬화. 파일 자체 쓰기는
    atomic rename이라 챕터 파일끼리도 충돌 없음.
    """
    out = chapters_dir(work_id) / f"{chapter_id}.json"
    _atomic_write_json(out, data)

    with _get_lock(work_id):
        state = load_state(work_id)
        entry = state["chapters"].get(chapter_id)
        if entry is None:
            raise KeyError(f"chapter not in state: {chapter_id}")
        entry["summary_status"] = "completed"
        entry["error"] = None
        save_state(work_id, state)

    return out


def save_extension_result(
    work_id: str,
    chapter_id: str,
    data: dict[str, Any],
) -> Path:
    """extension sub-agent 결과 저장 + state 갱신."""
    out = extensions_dir(work_id) / f"{chapter_id}.json"
    _atomic_write_json(out, data)

    with _get_lock(work_id):
        state = load_state(work_id)
        entry = state["chapters"].get(chapter_id)
        if entry is None:
            raise KeyError(f"chapter not in state: {chapter_id}")
        entry["extension_status"] = "completed"
        save_state(work_id, state)

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


# ---------------------------------------------------------------------------
# 조회 헬퍼
# ---------------------------------------------------------------------------

_DONE_STATUSES = ("completed", "skipped")


def list_pending_chapters_impl(work_id: str) -> dict[str, list[str]]:
    """summary/extension이 아직 처리되지 않은 챕터 ID 목록.

    `completed`와 `skipped`(비본문 챕터)는 처리 끝난 것으로 본다.

    Returns:
        {
            "summary_pending": ["ch1", "ch3"],
            "extension_pending": ["ch1", "ch2"],   # 옵션 비활성 필터링은 server에서
        }
    """
    state = load_state(work_id)
    summary_pending: list[str] = []
    extension_pending: list[str] = []
    for cid, entry in state.get("chapters", {}).items():
        if entry.get("summary_status") not in _DONE_STATUSES:
            summary_pending.append(cid)
        if entry.get("extension_status") not in _DONE_STATUSES:
            extension_pending.append(cid)
    return {
        "summary_pending": sorted(summary_pending),
        "extension_pending": sorted(extension_pending),
    }
