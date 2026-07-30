"""HTML 렌더러: state + book_info + chapters/{summaries,quiz,extension_quiz} → 정적 사이트.

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
import logging
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from .base import Renderer
from .page_labels import format_page_label
from .study_loader import _unescape_if_double_escaped, load_study_data

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
_STATIC_ASSET_FILES = ("style.css", "storage.js")
_STATIC_ROOT_FILES = ("study_html.py", "README.md")
_LAUNCHER_TEMPLATE_FILES = ("start_study.sh.template", "start_study.bat.template")
_PYTHON_EXECUTABLE_MARKER = "__PDF_STUDY_PYTHON__"


def _esc(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


# ---------------------------------------------------------------------------
# Asset / launcher 복사
# ---------------------------------------------------------------------------

def _copy_assets(output_dir: Path, python_executable: str) -> None:
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
    for template_name in _LAUNCHER_TEMPLATE_FILES:
        src = _TEMPLATES_DIR / template_name
        if not src.exists():
            raise FileNotFoundError(f"launcher template missing: {src}")
        template = src.read_text(encoding="utf-8")
        if _PYTHON_EXECUTABLE_MARKER not in template:
            raise ValueError(f"launcher template missing marker: {src}")
        if template_name.endswith(".sh.template"):
            executable = shlex.quote(python_executable)
        else:
            executable = '"' + python_executable.replace('"', '""') + '"'
        output_path = output_dir / template_name.removesuffix(".template")
        output_path.write_text(
            template.replace(_PYTHON_EXECUTABLE_MARKER, executable), encoding="utf-8"
        )
        if template_name.endswith(".sh.template"):
            output_path.chmod(0o755)


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


def _chapter_controls(
    chapters: list[dict[str, Any]],
    index: int,
    *,
    show_navigation: bool,
) -> str:
    """스크롤을 따라오는 챕터 이동·완료 통합 컨트롤."""
    navigation = ""
    if show_navigation:
        if index > 0:
            prev = chapters[index - 1]["chapter_id"]
            prev_control = (
                f'<a class="chapter-control" href="{_esc(prev)}.html" '
                'aria-label="이전 챕터">'
                '<span class="control-icon" aria-hidden="true">←</span>'
                '<span class="control-label">이전 챕터</span>'
                '</a>'
            )
        else:
            prev_control = (
                '<span class="chapter-control is-disabled" aria-disabled="true">'
                '<span class="control-icon" aria-hidden="true">←</span>'
                '<span class="control-label">이전 챕터</span>'
                '</span>'
            )

        if index < len(chapters) - 1:
            nxt = chapters[index + 1]["chapter_id"]
            next_control = (
                f'<a class="chapter-control" href="{_esc(nxt)}.html" '
                'aria-label="다음 챕터">'
                '<span class="control-icon" aria-hidden="true">→</span>'
                '<span class="control-label">다음 챕터</span>'
                '</a>'
            )
        else:
            next_control = (
                '<span class="chapter-control is-disabled" aria-disabled="true">'
                '<span class="control-icon" aria-hidden="true">→</span>'
                '<span class="control-label">다음 챕터</span>'
                '</span>'
            )

        navigation = (
            '<nav class="chapter-controls-nav" aria-label="챕터 이동">'
            f'{prev_control}'
            '<a class="chapter-control" href="index.html" aria-label="목차">'
            '<span class="control-icon" aria-hidden="true">☰</span>'
            '<span class="control-label">목차</span>'
            '</a>'
            f'{next_control}'
            '</nav>'
            '<div class="control-divider" aria-hidden="true"></div>'
        )

    return (
        '<aside class="chapter-controls" aria-label="챕터 도구">'
        f'{navigation}'
        '<button type="button" class="chapter-control complete-btn" aria-pressed="false">'
        '<span class="check control-icon" aria-hidden="true"></span>'
        '<span class="label control-label">이 챕터 완료로 표시</span>'
        '</button>'
        '</aside>'
    )


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
            f'<button type="button" class="reveal" aria-expanded="false">모범답안 보기</button>'
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
        model_answer = q.get("model_answer") or ""
        parts.append(
            f'<div class="question text" data-qid="{qid}">'
            f'<p class="q-prompt">{_esc(q.get("question") or "")}</p>'
            f'<textarea placeholder="여기에 답변을 입력하세요"></textarea>'
            f'<button type="button" class="reveal" aria-expanded="false">모범답안 보기</button>'
            f'<div class="model-answer" hidden>{_esc(model_answer)}</div>'
            '</div>'
        )
    parts.append("</section>")
    return "".join(parts)


def _chapter_body(
    chapter: dict[str, Any],
    opts: dict[str, bool],
    *,
    page_offset: int | None = None,
) -> str:
    meta = chapter["meta"]
    summary = chapter.get("summary") or {}
    extension = chapter.get("extension") or {}
    questions = (summary.get("questions") or {})

    title = summary.get("title") or meta.get("title") or chapter["chapter_id"]
    page_label = format_page_label(meta, page_offset=page_offset)
    range_html = f'<p class="page-range">{_esc(page_label)}</p>' if page_label else ""

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
    body_classes: list[str] = []
    if sidebar_html:
        body_classes.append("has-sidebar")
    if page_kind == "chapter":
        body_classes.append("has-chapter-controls")
    body_class = " ".join(body_classes)
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
        '</body></html>'
    )


def _index_body(
    book_info: dict[str, Any],
    chapters: list[dict[str, Any]],
    *,
    page_offset: int | None = None,
) -> str:
    header = _book_info_header(book_info)
    items: list[str] = []
    for ch in chapters:
        cid = ch["chapter_id"]
        meta = ch["meta"]
        title = (ch.get("summary") or {}).get("title") or meta.get("title") or cid
        page_label = format_page_label(meta, page_offset=page_offset)
        items.append(
            f'<a class="chapter-link" href="{_esc(cid)}.html" data-chapter="{_esc(cid)}">'
            '<div class="row">'
            '<span class="chapter-check" aria-hidden="true"></span>'
            f'<span class="chapter-title">{_esc(title)}</span>'
            f'<span class="chapter-range">{_esc(page_label)}</span>'
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
        loaded = load_study_data(work_id)
        state = loaded["state"]
        book_info = loaded["book_info"]
        chapters = loaded["chapters"]

        if not chapters:
            raise RuntimeError("no chapters to render. did set_chapters run?")

        opts = state.get("question_options") or {}
        page_offset = state.get("page_offset")
        output_dir.mkdir(parents=True, exist_ok=True)
        _copy_assets(output_dir, sys.executable)

        single = len(chapters) == 1
        book_title = book_info.get("title") or "Study"

        if single:
            ch = chapters[0]
            body = _book_info_header(book_info) + (
                '<article>' + _chapter_body(ch, opts, page_offset=page_offset) + '</article>'
            ) + _chapter_controls(chapters, 0, show_navigation=False)
            html_text = _page_shell(
                lang="ko",
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
            idx_body = _index_body(book_info, chapters, page_offset=page_offset)
            (output_dir / "index.html").write_text(
                _page_shell(
                    lang="ko",
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
                    '<article>' + _chapter_body(ch, opts, page_offset=page_offset) + '</article>'
                    + _chapter_controls(chapters, i, show_navigation=True)
                )
                (output_dir / f"{cid}.html").write_text(
                    _page_shell(
                        lang="ko",
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
