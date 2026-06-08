"""HTML 렌더러: state + book_info + chapters/{summaries,quiz,extension_questions} → 정적 사이트.

설계 메모:
- Jinja2 의존 추가하지 않고 stdlib `html.escape` + f-string 합성.
- assets/는 templates/html의 static 파일 복사.
- 단일 챕터는 index.html 생략 → main.html 하나에 책 정보 상단 부착.
- 옵션 비활성 유형은 섹션 자체를 생략 (sub-agent도 생성하지 않으므로 빈 섹션 없음).
- 요약은 마크다운 → HTML로 렌더한다(markdown-it-py, 없으면 내장 폴백). 그림(figure)은
  더 이상 다루지 않는다.
"""
from __future__ import annotations

import html
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from .. import workspace
from ..prompts import _chapter_sort_key  # 내부 헬퍼 재사용
from .base import Renderer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 마크다운 → HTML
#
# 정상 경로: markdown-it-py를 쓴다. 이건 rich(핵심 의존성)가 항상 끌고 오는
# 전이 의존성이라 서버 venv엔 사실상 늘 깔려 있어, **사용자가 따로 설치할 필요가
# 없다**. 그래도 어떤 이유로든 없을 때를 대비해(=절대 설치를 강요하지 않도록),
# study_tui.py가 rich 없이 평문 셰임으로 폴백하듯, 여기서도 내장 폴백 변환기로
# 떨어진다. 폴백도 raw 마크다운을 그대로 노출하지 않고 읽을 수 있는 HTML로 바꾼다.
# ---------------------------------------------------------------------------

class _FallbackMd:
    """markdown-it-py가 없을 때 쓰는 최소 마크다운→HTML 변환기.

    완전한 CommonMark는 아니지만 요약에서 흔한 문법(제목·굵게·기울임·인라인
    코드·코드펜스·목록·인용·링크·표)을 HTML로 바꿔 'raw 텍스트 노출'을 막는다.
    markdown-it과 동일한 render()/renderInline() 인터페이스를 흉내 낸다.
    """

    _CODE = re.compile(r"`([^`]+)`")
    _LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
    _BOLD = re.compile(r"\*\*(.+?)\*\*")
    _ITAL = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
    _ITAL_US = re.compile(r"(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?!_)")

    def renderInline(self, src: str) -> str:
        return self._inline(src or "")

    def _inline(self, text: str) -> str:
        # 1) 인라인 코드 보호 (escape 후 placeholder로 치환)
        codes: list[str] = []

        def _stash(m: re.Match) -> str:
            codes.append(html.escape(m.group(1)))
            return f"\x00{len(codes) - 1}\x00"

        text = self._CODE.sub(_stash, text)
        # 2) 나머지 escape
        text = html.escape(text)
        # 3) 링크 → a 태그
        text = self._LINK.sub(
            lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
            text,
        )
        # 4) 굵게 → 기울임
        text = self._BOLD.sub(r"<strong>\1</strong>", text)
        text = self._ITAL.sub(r"<em>\1</em>", text)
        text = self._ITAL_US.sub(r"<em>\1</em>", text)
        # 5) 코드 복원
        text = re.sub(r"\x00(\d+)\x00",
                      lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)
        return text

    @staticmethod
    def _cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    def render(self, src: str) -> str:  # noqa: C901 - 단순 블록 스캐너
        lines = (src or "").replace("\r\n", "\n").split("\n")
        out: list[str] = []
        para: list[str] = []
        list_tag: str | None = None
        i, n = 0, len(lines)

        def flush_para() -> None:
            if para:
                out.append("<p>" + self._inline(" ".join(para).strip()) + "</p>")
                para.clear()

        def close_list() -> None:
            nonlocal list_tag
            if list_tag:
                out.append(f"</{list_tag}>")
                list_tag = None

        while i < n:
            raw = lines[i]
            s = raw.strip()

            # 코드펜스
            m = re.match(r"^```(\w*)\s*$", s)
            if m:
                flush_para(); close_list()
                lang, i = m.group(1), i + 1
                buf: list[str] = []
                while i < n and not re.match(r"^```\s*$", lines[i].strip()):
                    buf.append(lines[i]); i += 1
                i += 1  # 닫는 펜스 skip
                cls = f' class="language-{lang}"' if lang else ""
                out.append(f"<pre><code{cls}>" + html.escape("\n".join(buf)) + "\n</code></pre>")
                continue

            if not s:
                flush_para(); close_list(); i += 1; continue

            # 제목
            m = re.match(r"^(#{1,6})\s+(.*)$", s)
            if m:
                flush_para(); close_list()
                lvl = len(m.group(1))
                out.append(f"<h{lvl}>{self._inline(m.group(2).strip())}</h{lvl}>")
                i += 1; continue

            # 인용
            if s.startswith(">"):
                flush_para(); close_list()
                buf = []
                while i < n and lines[i].strip().startswith(">"):
                    buf.append(re.sub(r"^>\s?", "", lines[i].strip())); i += 1
                out.append("<blockquote>" + self._inline(" ".join(buf).strip()) + "</blockquote>")
                continue

            # 표 (GFM): 헤더 + 구분선
            if "|" in s and i + 1 < n and "-" in lines[i + 1] and \
                    re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
                flush_para(); close_list()
                header = self._cells(s); i += 2
                rows = []
                while i < n and "|" in lines[i] and lines[i].strip():
                    rows.append(self._cells(lines[i])); i += 1
                thead = "".join(f"<th>{self._inline(c)}</th>" for c in header)
                body = "".join(
                    "<tr>" + "".join(f"<td>{self._inline(c)}</td>" for c in r) + "</tr>"
                    for r in rows
                )
                out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>")
                continue

            # 순서 없는 목록
            m = re.match(r"^[-*+]\s+(.*)$", s)
            if m:
                flush_para()
                if list_tag != "ul":
                    close_list(); out.append("<ul>"); list_tag = "ul"
                out.append("<li>" + self._inline(m.group(1).strip()) + "</li>")
                i += 1; continue

            # 순서 있는 목록
            m = re.match(r"^\d+\.\s+(.*)$", s)
            if m:
                flush_para()
                if list_tag != "ol":
                    close_list(); out.append("<ol>"); list_tag = "ol"
                out.append("<li>" + self._inline(m.group(1).strip()) + "</li>")
                i += 1; continue

            para.append(s); i += 1

        flush_para(); close_list()
        return "\n".join(out)


try:
    from markdown_it import MarkdownIt

    # html=False라 본문 내 원시 HTML은 이스케이프되어 안전. 표(GFM)만 추가 활성화.
    # (rich TUI와 동일 파서 계열이라 렌더 결과가 일관됨)
    _MD: Any = MarkdownIt("commonmark", {"html": False}).enable("table")
except ImportError:  # rich가 정상 설치되면 늘 존재하지만, 만일을 위한 폴백
    logger.warning("markdown-it-py 미설치 — 내장 폴백 마크다운 변환기 사용")
    _MD = _FallbackMd()

# 요약 본문 안의 헤딩은 섹션 제목('요약' h2) 아래로 한 단계 낮춰 계층을 맞춘다.
_HEADING_RE = re.compile(r"(</?)h([1-6])\b")


def _demote_headings(html_text: str) -> str:
    return _HEADING_RE.sub(
        lambda m: f"{m.group(1)}h{min(int(m.group(2)) + 1, 6)}", html_text
    )


# templates 디렉터리 (이 파일과 동일 패키지의 templates/html)
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "html"
_STATIC_ASSET_FILES = ("style.css", "grading.js", "storage.js")
_STATIC_ROOT_FILES = ("study_html.py", "README.md")


def _esc(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


# ---------------------------------------------------------------------------
# 데이터 로딩
# ---------------------------------------------------------------------------

def _load_all(work_id: str) -> dict[str, Any]:
    state = workspace.load_state(work_id)
    book_info = workspace.load_book_info(work_id) or {}
    summaries_dir = workspace.summaries_dir(work_id)
    quiz_dir = workspace.quiz_dir(work_id)
    ext_dir = workspace.extension_questions_dir(work_id)
    raw_dir = workspace.chapters_raw_dir(work_id)

    all_ids = sorted(state.get("chapters", {}).keys(), key=_chapter_sort_key)
    # skipped 챕터(찾아보기·색인 등 비본문)는 렌더 대상에서 제외
    chapter_ids = [cid for cid in all_ids if not state["chapters"][cid].get("skip")]
    chapters: list[dict[str, Any]] = []
    for cid in chapter_ids:
        meta = state["chapters"][cid]
        sum_path = summaries_dir / f"{cid}.json"
        quiz_path = quiz_dir / f"{cid}.json"
        ext_path = ext_dir / f"{cid}.json"
        raw_path = raw_dir / f"{cid}.json"

        summary_data = json.loads(sum_path.read_text(encoding="utf-8")) if sum_path.exists() else None
        quiz_data = json.loads(quiz_path.read_text(encoding="utf-8")) if quiz_path.exists() else None
        ext_data = json.loads(ext_path.read_text(encoding="utf-8")) if ext_path.exists() else None
        raw_data = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else None

        # summaries(요약) + quiz(문제)는 분리 저장되지만 다운스트림은 한 dict로 본다.
        if summary_data is not None or quiz_data is not None:
            summary_data = summary_data or {}
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


# ---------------------------------------------------------------------------
# Asset / launcher 복사
# ---------------------------------------------------------------------------

def _copy_assets(output_dir: Path) -> None:
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for name in _STATIC_ASSET_FILES:
        src = _TEMPLATES_DIR / name
        if not src.exists():
            raise FileNotFoundError(f"template asset missing: {src}")
        shutil.copyfile(src, assets_dir / name)
    for name in _STATIC_ROOT_FILES:
        src = _TEMPLATES_DIR / name
        if not src.exists():
            raise FileNotFoundError(f"template file missing: {src}")
        shutil.copyfile(src, output_dir / name)


# ---------------------------------------------------------------------------
# HTML 빌더 — 부분
# ---------------------------------------------------------------------------

def _book_info_header(book_info: dict[str, Any]) -> str:
    title = _esc(book_info.get("title") or "Untitled")
    parts_meta: list[str] = []
    author = book_info.get("author")
    publisher = book_info.get("publisher")
    year = book_info.get("publication_year")
    if author:
        parts_meta.append(_esc(author))
    if publisher:
        parts_meta.append(_esc(publisher))
    if year:
        parts_meta.append(f"({_esc(year)})")
    meta_line = " · ".join(parts_meta)

    preface = book_info.get("preface_summary")
    preface_html = ""
    if preface:
        preface_html = (
            '<details class="preface">'
            f'<summary>책 소개</summary><p>{_esc(preface)}</p>'
            '</details>'
        )

    return (
        '<header class="book-info">'
        f'<h1>{title}</h1>'
        f'<p class="meta">{meta_line}</p>'
        f'{preface_html}'
        '</header>'
    )


def _chapter_nav_links(chapters: list[dict[str, Any]], index: int, show_index_link: bool) -> str:
    parts: list[str] = []
    if index > 0:
        prev = chapters[index - 1]["chapter_id"]
        parts.append(f'<a href="{_esc(prev)}.html">← 이전 챕터</a>')
    else:
        parts.append("<span></span>")
    if show_index_link:
        parts.append('<a href="index.html">목차</a>')
    if index < len(chapters) - 1:
        nxt = chapters[index + 1]["chapter_id"]
        parts.append(f'<a href="{_esc(nxt)}.html">다음 챕터 →</a>')
    else:
        parts.append("<span></span>")
    return '<nav class="chapter-nav">' + "".join(parts) + "</nav>"


def _summary_section(summary: dict[str, Any]) -> str:
    """요약을 마크다운 → HTML로 렌더. 헤딩은 섹션 제목 아래로 한 단계 낮춘다."""
    text = str(summary.get("summary") or "")
    key_points = summary.get("key_points") or []

    body_html = _demote_headings(_MD.render(text))

    parts = [f'<section id="summary"><h2>요약</h2>{body_html}</section>']

    if key_points:
        parts.append('<section id="key-points"><h2>핵심 포인트</h2><ul>')
        for kp in key_points:
            # 핵심 포인트도 인라인 마크다운(굵게·코드 등) 허용
            parts.append(f"<li>{_MD.renderInline(str(kp))}</li>")
        parts.append("</ul></section>")
    return "".join(parts)


def _mc_section(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    parts = ['<section id="mc"><h2>객관식</h2>',
             '<p class="mc-summary">객관식 정답 <strong>0</strong> / 0</p>']
    for q in items:
        qid = _esc(q.get("id") or "")
        ans_idx = int(q.get("answer_index", 0))
        opts_html = []
        for i, opt in enumerate(q.get("options") or []):
            opts_html.append(
                '<label>'
                f'<input type="radio" name="{qid}" value="{i}">'
                f'<span>{_esc(opt)}</span>'
                '</label>'
            )
        explanation = q.get("explanation") or ""
        parts.append(
            f'<div class="question mc" data-qid="{qid}" data-answer="{ans_idx}">'
            f'<p class="q-prompt">{_esc(q.get("question") or "")}</p>'
            f'<div class="options">{"".join(opts_html)}</div>'
            f'<div class="explanation" hidden>{_esc(explanation)}</div>'
            '</div>'
        )
    parts.append("</section>")
    return "".join(parts)


def _text_section(section_id: str, title: str, items: list[dict[str, Any]], kind: str) -> str:
    """단답/주관 공통."""
    if not items:
        return ""
    parts = [f'<section id="{section_id}"><h2>{_esc(title)}</h2>']
    for q in items:
        qid = _esc(q.get("id") or "")
        model_answer = q.get("model_answer") or ""
        parts.append(
            f'<div class="question text" data-qid="{qid}">'
            f'<p class="q-prompt">{_esc(q.get("question") or "")}</p>'
            f'<textarea placeholder="여기에 답변을 입력하세요"></textarea>'
            f'<button type="button" class="reveal">모범답안 보기</button>'
            f'<div class="model-answer" hidden>{_esc(model_answer)}</div>'
            '</div>'
        )
    parts.append("</section>")
    return "".join(parts)


def _extension_section(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    parts = ['<section id="ex"><h2>확장형</h2>']
    for q in items:
        qid = _esc(q.get("id") or "")
        context = q.get("context") or ""
        model_answer = q.get("model_answer") or ""
        sources = q.get("sources") or []
        ctx_html = f'<div class="ext-context">{_esc(context)}</div>' if context else ""
        sources_html = ""
        if sources:
            links = "".join(
                f'<li><a href="{_esc(u)}" target="_blank" rel="noopener">{_esc(u)}</a></li>'
                for u in sources
            )
            sources_html = f'<ul class="sources">{links}</ul>'
        parts.append(
            f'<div class="question text" data-qid="{qid}">'
            f'<p class="q-prompt">{_esc(q.get("question") or "")}</p>'
            f'{ctx_html}'
            f'<textarea placeholder="여기에 답변을 입력하세요"></textarea>'
            f'<button type="button" class="reveal">모범답안 보기</button>'
            f'<div class="model-answer" hidden>{_esc(model_answer)}</div>'
            f'{sources_html}'
            '</div>'
        )
    parts.append("</section>")
    return "".join(parts)


def _chapter_body(
    chapter: dict[str, Any],
    opts: dict[str, bool],
) -> str:
    meta = chapter["meta"]
    summary = chapter.get("summary") or {}
    extension = chapter.get("extension") or {}
    questions = (summary.get("questions") or {})

    title = summary.get("title") or meta.get("title") or chapter["chapter_id"]
    page_range = meta.get("page_range") or summary.get("page_range")
    range_html = ""
    if isinstance(page_range, (list, tuple)) and len(page_range) == 2:
        range_html = f'<p class="page-range">p.{_esc(page_range[0])}–{_esc(page_range[1])}</p>'

    sections = [
        f'<h1>{_esc(title)}</h1>',
        range_html,
        _summary_section(summary),
    ]
    if opts.get("multiple_choice"):
        sections.append(_mc_section(questions.get("multiple_choice") or []))
    if opts.get("short_answer"):
        sections.append(_text_section("sa", "단답형", questions.get("short_answer") or [], "sa"))
    if opts.get("reflection"):
        sections.append(_text_section("rf", "주관식", questions.get("reflection") or [], "rf"))
    if opts.get("extension"):
        ext_items = ((extension.get("questions") or {}).get("extension") or [])
        sections.append(_extension_section(ext_items))

    # 챕터 끝에 명시적 완료 토글
    sections.append(
        '<section class="completion-control">'
        '<button type="button" class="complete-btn" aria-pressed="false">'
        '<span class="check" aria-hidden="true"></span>'
        '<span class="label">이 챕터 완료로 표시</span>'
        '</button>'
        '</section>'
    )

    return "".join(sections)


# ---------------------------------------------------------------------------
# 페이지 빌더
# ---------------------------------------------------------------------------

def _sidebar(
    book_title: str,
    chapters: list[dict[str, Any]],
    current_id: str | None,
) -> str:
    """챕터 사이드바: 책 제목 + 챕터 링크 + 완료 표시 placeholder.

    completed 여부는 storage.js가 GET /api/progress/{cid}로 채운다.
    """
    items: list[str] = []
    for ch in chapters:
        cid = ch["chapter_id"]
        title = (ch.get("summary") or {}).get("title") or ch["meta"].get("title") or cid
        active_attr = ' aria-current="page"' if cid == current_id else ""
        active_class = " is-active" if cid == current_id else ""
        items.append(
            f'<a class="sidebar-link{active_class}" '
            f'data-chapter="{_esc(cid)}" href="{_esc(cid)}.html"{active_attr}>'
            '<span class="sidebar-check" aria-hidden="true"></span>'
            f'<span class="sidebar-title">{_esc(title)}</span>'
            '</a>'
        )
    return (
        '<button class="sidebar-toggle" type="button" '
        'aria-label="목차 토글" aria-controls="sidebar" aria-expanded="false">☰</button>'
        '<aside class="sidebar" id="sidebar" aria-label="챕터 목차">'
        f'<a class="sidebar-book" href="index.html">{_esc(book_title)}</a>'
        '<nav class="sidebar-chapters">' + "".join(items) + "</nav>"
        "</aside>"
        '<div class="sidebar-scrim" hidden></div>'
    )


def _page_shell(
    *,
    lang: str,
    title: str,
    body: str,
    page_kind: str,
    chapter_id: str | None = None,
    sidebar_html: str = "",
) -> str:
    data_attrs = f'data-page="{_esc(page_kind)}"'
    if chapter_id:
        data_attrs += f' data-chapter-id="{_esc(chapter_id)}"'
    body_class = "has-sidebar" if sidebar_html else ""
    return (
        f'<!DOCTYPE html><html lang="{_esc(lang)}"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_esc(title)}</title>'
        '<link rel="stylesheet" href="assets/style.css">'
        f'</head><body class="{body_class}">'
        f'{sidebar_html}'
        f'<main {data_attrs}>'
        f'{body}'
        '</main>'
        '<script src="assets/storage.js" defer></script>'
        '<script src="assets/grading.js" defer></script>'
        '</body></html>'
    )


def _index_body(book_info: dict[str, Any], chapters: list[dict[str, Any]]) -> str:
    header = _book_info_header(book_info)
    items: list[str] = []
    for ch in chapters:
        cid = ch["chapter_id"]
        meta = ch["meta"]
        title = (ch.get("summary") or {}).get("title") or meta.get("title") or cid
        pr = meta.get("page_range") or [0, 0]
        items.append(
            f'<a class="chapter-link" href="{_esc(cid)}.html" data-chapter="{_esc(cid)}">'
            '<div class="row">'
            '<span class="chapter-check" aria-hidden="true"></span>'
            f'<span class="chapter-title">{_esc(title)}</span>'
            f'<span class="chapter-range">p.{_esc(pr[0])}–{_esc(pr[1])}</span>'
            "</div>"
            '<div class="status-text">아직 학습하지 않음</div>'
            '</a>'
        )
    return header + '<nav class="chapter-list">' + "".join(items) + "</nav>"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class HtmlRenderer(Renderer):
    def render(self, work_id: str, output_dir: Path) -> None:
        loaded = _load_all(work_id)
        state = loaded["state"]
        book_info = loaded["book_info"]
        chapters = loaded["chapters"]

        if not chapters:
            raise RuntimeError("no chapters to render. did set_chapters run?")

        opts = state.get("question_options") or {}
        language = (state.get("language") or "en").lower()
        if language not in ("ko", "en"):
            language = "en"

        output_dir.mkdir(parents=True, exist_ok=True)
        _copy_assets(output_dir)

        single = len(chapters) == 1
        book_title = book_info.get("title") or "Study"

        if single:
            ch = chapters[0]
            body = _book_info_header(book_info) + (
                '<article>' + _chapter_body(ch, opts) + '</article>'
            )
            html_text = _page_shell(
                lang=language,
                title=str(book_title),
                body=body,
                page_kind="chapter",
                chapter_id=ch["chapter_id"],
            )
            (output_dir / "main.html").write_text(html_text, encoding="utf-8")
            # index.html이 있으면 단일 챕터 모드와 충돌하므로 제거
            idx = output_dir / "index.html"
            if idx.exists():
                idx.unlink()
        else:
            # index.html
            idx_body = _index_body(book_info, chapters)
            (output_dir / "index.html").write_text(
                _page_shell(
                    lang=language,
                    title=str(book_title),
                    body=idx_body,
                    page_kind="index",
                ),
                encoding="utf-8",
            )
            # 각 챕터 페이지 — 사이드바는 챕터 페이지에만 (index/단일챕터는 불필요)
            for i, ch in enumerate(chapters):
                cid = ch["chapter_id"]
                ch_title = (ch.get("summary") or {}).get("title") or ch["meta"].get("title") or cid
                article_body = (
                    _chapter_nav_links(chapters, i, show_index_link=True)
                    + '<article>' + _chapter_body(ch, opts) + '</article>'
                    + _chapter_nav_links(chapters, i, show_index_link=True)
                )
                (output_dir / f"{cid}.html").write_text(
                    _page_shell(
                        lang=language,
                        title=f"{book_title} · {ch_title}",
                        body=article_body,
                        page_kind="chapter",
                        chapter_id=cid,
                        sidebar_html=_sidebar(str(book_title), chapters, cid),
                    ),
                    encoding="utf-8",
                )

        # progress 폴더 자리만 만들어 둠 (study_html.py가 첫 GET 시 생성하기도 함)
        (output_dir / "progress").mkdir(exist_ok=True)
