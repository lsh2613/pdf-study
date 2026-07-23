"""출력 형식에 중립적인 렌더 입력 로더."""
from __future__ import annotations

import json
from typing import Any

from .. import workspace


def load_study_data(work_id: str) -> dict[str, Any]:
    """현재 상태에서 렌더 가능한 중립 학습 데이터를 반환한다."""
    state = workspace.load_state(work_id)
    book_info = workspace.load_book_info(work_id) or {}
    summaries_dir = workspace.summaries_dir(work_id)
    quiz_dir = workspace.quiz_dir(work_id)
    ext_dir = workspace.extension_quiz_dir(work_id)
    raw_dir = workspace.chapters_raw_dir(work_id)

    all_ids = sorted(state.get("chapters", {}).keys(), key=_chapter_sort_key)
    chapter_ids = [cid for cid in all_ids if not state["chapters"][cid].get("skip")]
    chapters: list[dict[str, Any]] = []
    for cid in chapter_ids:
        meta = state["chapters"][cid]
        sum_path = summaries_dir / f"{cid}.json"
        quiz_path = quiz_dir / f"{cid}.json"
        ext_path = ext_dir / f"{cid}.json"
        raw_path = raw_dir / f"{cid}.json"

        summary_completed = meta.get("summary_status") == "completed"
        extension_completed = meta.get("extension_status") == "completed"
        summary_data = (
            json.loads(sum_path.read_text(encoding="utf-8"))
            if summary_completed and sum_path.exists()
            else None
        )
        quiz_data = (
            json.loads(quiz_path.read_text(encoding="utf-8"))
            if summary_completed and quiz_path.exists()
            else None
        )
        ext_data = (
            json.loads(ext_path.read_text(encoding="utf-8"))
            if extension_completed and ext_path.exists()
            else None
        )
        raw_data = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else None

        if summary_data is not None or quiz_data is not None:
            summary_data = summary_data or {}
            if isinstance(summary_data.get("summary"), str):
                summary_data["summary"] = _unescape_if_double_escaped(summary_data["summary"])
            summary_data["questions"] = (quiz_data or {}).get("questions") or {}

        chapters.append({
            "chapter_id": cid,
            "meta": meta,
            "summary": summary_data,
            "extension": ext_data,
            "raw": raw_data,
        })

    return {
        "state": state,
        "book_info": book_info,
        "chapters": chapters,
    }


def _chapter_sort_key(chapter_id: str) -> tuple[int, str]:
    if chapter_id.startswith("ch") and chapter_id[2:].isdigit():
        return (int(chapter_id[2:]), chapter_id)
    return (10**9, chapter_id)


def _unescape_if_double_escaped(text: str) -> str:
    r"""실제 개행 없이 리터럴 `\n`만 있는 요약을 렌더 전 복구한다."""
    if "\n" in text:
        return text
    if "\\n" in text or "\\t" in text:
        return (text.replace("\\r\\n", "\n")
                    .replace("\\n", "\n")
                    .replace("\\t", "\t"))
    return text
