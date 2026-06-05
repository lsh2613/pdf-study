"""Markdown + TUI 렌더러: 챕터별 폴더 + summary.md + quiz.json + TUI launcher.

설계 메모 (docs/05-data-schemas.md, docs/07-study-ui.md):
- 출력 포맷 중립 JSON(chapters/, extensions/)을 읽어 챕터별 폴더로 전개.
- summary.md = 읽기 전용(요약/핵심포인트), quiz.json = TUI 전용(4유형 문제+정답).
- 엔진(study_tui.py)은 출력 루트에 1벌 복사, 각 챕터엔 엔진을 호출하는 얇은 launcher.
- 옵션 비활성 유형은 quiz.json에서 생략 (sub-agent도 생성하지 않으므로 빈 유형 없음).
- 데이터 로딩은 HtmlRenderer와 동일 소스 → _load_all 재사용.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .base import Renderer
from .html_renderer import _load_all  # 동일한 state/book_info/chapters 로더 재사용

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "md_tui"


# ---------------------------------------------------------------------------
# Markdown 빌더
# ---------------------------------------------------------------------------

def _chapter_title(chapter: dict[str, Any]) -> str:
    summary = chapter.get("summary") or {}
    return summary.get("title") or chapter["meta"].get("title") or chapter["chapter_id"]


def _book_md(book_info: dict[str, Any], chapters: list[dict[str, Any]]) -> str:
    lines = [f"# {book_info.get('title') or 'Study'}", ""]

    meta_parts: list[str] = []
    if book_info.get("author"):
        meta_parts.append(str(book_info["author"]))
    if book_info.get("publisher"):
        meta_parts.append(str(book_info["publisher"]))
    if book_info.get("publication_year"):
        meta_parts.append(f"({book_info['publication_year']})")
    if meta_parts:
        lines += [" · ".join(meta_parts), ""]

    if book_info.get("preface_summary"):
        lines += [f"> {book_info['preface_summary']}", ""]

    lines.append("## 챕터")
    for ch in chapters:
        cid = ch["chapter_id"]
        pr = ch["meta"].get("page_range") or [0, 0]
        lines.append(f"- [{_chapter_title(ch)}]({cid}/summary.md) — p.{pr[0]}–{pr[1]}")
    return "\n".join(lines) + "\n"


def _summary_md(chapter: dict[str, Any]) -> str:
    summary = chapter.get("summary") or {}
    lines = [f"# {_chapter_title(chapter)}"]

    pr = chapter["meta"].get("page_range") or summary.get("page_range")
    if isinstance(pr, (list, tuple)) and len(pr) == 2:
        lines.append(f"> p.{pr[0]}–{pr[1]}")
    lines.append("")

    # 요약은 이미 마크다운 — 그대로 둔다(rich가 렌더).
    lines.append(str(summary.get("summary") or "").strip())
    lines.append("")

    key_points = summary.get("key_points") or []
    if key_points:
        lines.append("## 핵심 포인트")
        lines += [f"- {kp}" for kp in key_points]
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _quiz_data(chapter: dict[str, Any], opts: dict[str, bool]) -> dict[str, Any]:
    """summary.questions + extension을 옵션 필터링해 TUI용 quiz.json 페이로드로 병합."""
    src = (chapter.get("summary") or {}).get("questions") or {}
    ext = ((chapter.get("extension") or {}).get("questions") or {}).get("extension") or []

    questions: dict[str, Any] = {}
    if opts.get("multiple_choice"):
        questions["multiple_choice"] = src.get("multiple_choice") or []
    if opts.get("short_answer"):
        questions["short_answer"] = src.get("short_answer") or []
    if opts.get("reflection"):
        questions["reflection"] = src.get("reflection") or []
    if opts.get("extension"):
        questions["extension"] = ext

    return {
        "chapter_id": chapter["chapter_id"],
        "title": _chapter_title(chapter),
        "questions": questions,
    }


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class MdTuiRenderer(Renderer):
    def render(self, work_id: str, output_dir: Path) -> None:
        loaded = _load_all(work_id)
        state = loaded["state"]
        book_info = loaded["book_info"]
        chapters = loaded["chapters"]

        if not chapters:
            raise RuntimeError("no chapters to render. did set_chapters run?")

        opts = state.get("question_options") or {}
        output_dir.mkdir(parents=True, exist_ok=True)

        # 엔진 + launcher 템플릿 + README
        engine_src = _TEMPLATES_DIR / "study_tui.py"
        launcher_src = _TEMPLATES_DIR / "chapter_launcher.py"
        readme_src = _TEMPLATES_DIR / "README.md"
        for src in (engine_src, launcher_src, readme_src):
            if not src.exists():
                raise FileNotFoundError(f"md_tui template missing: {src}")
        shutil.copyfile(engine_src, output_dir / "study_tui.py")
        shutil.copyfile(readme_src, output_dir / "README.md")
        launcher_text = launcher_src.read_text(encoding="utf-8")

        (output_dir / "book.md").write_text(
            _book_md(book_info, chapters), encoding="utf-8"
        )

        for ch in chapters:
            ch_dir = output_dir / ch["chapter_id"]
            ch_dir.mkdir(parents=True, exist_ok=True)
            (ch_dir / "summary.md").write_text(_summary_md(ch), encoding="utf-8")
            (ch_dir / "quiz.json").write_text(
                json.dumps(_quiz_data(ch, opts), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (ch_dir / "study_tui.py").write_text(launcher_text, encoding="utf-8")
