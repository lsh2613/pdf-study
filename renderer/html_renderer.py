"""HTML 렌더러: state + book_info + chapters + extensions → 정적 사이트.

설계 메모:
- Jinja2 의존 추가하지 않고 stdlib `html.escape` + f-string 합성.
- assets/는 templates/html의 static 파일 복사.
- 단일 챕터는 index.html 생략 → main.html 하나에 책 정보 상단 부착.
- 옵션 비활성 유형은 섹션 자체를 생략 (sub-agent도 생성하지 않으므로 빈 섹션 없음).
- 이미지: chapters_raw의 image_refs → output_dir/images/로 복사 + 챕터 페이지에 figure로 표시.
"""
from __future__ import annotations

import html
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .. import workspace
from ..prompts import _chapter_sort_key  # 내부 헬퍼 재사용
from .base import Renderer

logger = logging.getLogger(__name__)

# templates 디렉터리 (이 파일과 동일 패키지의 templates/html)
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "html"
_STATIC_ASSET_FILES = ("style.css", "grading.js", "storage.js")
_STATIC_ROOT_FILES = ("serve.py", "README.md")


def _esc(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


# ---------------------------------------------------------------------------
# 데이터 로딩
# ---------------------------------------------------------------------------

def _load_all(work_id: str) -> dict[str, Any]:
    state = workspace.load_state(work_id)
    book_info = workspace.load_book_info(work_id) or {}
    chapters_dir = workspace.chapters_dir(work_id)
    extensions_dir = workspace.extensions_dir(work_id)
    raw_dir = workspace.chapters_raw_dir(work_id)

    chapter_ids = sorted(state.get("chapters", {}).keys(), key=_chapter_sort_key)
    chapters: list[dict[str, Any]] = []
    for cid in chapter_ids:
        meta = state["chapters"][cid]
        ch_path = chapters_dir / f"{cid}.json"
        ext_path = extensions_dir / f"{cid}.json"
        raw_path = raw_dir / f"{cid}.json"

        summary_data = json.loads(ch_path.read_text(encoding="utf-8")) if ch_path.exists() else None
        ext_data = json.loads(ext_path.read_text(encoding="utf-8")) if ext_path.exists() else None
        raw_data = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else None

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
# 이미지 복사
# ---------------------------------------------------------------------------

def _copy_chapter_images(chapters: list[dict[str, Any]], output_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """raw image_refs → output_dir/images/로 복사. chapter_id → image meta 매핑 반환."""
    out_images = output_dir / "images"
    out_images.mkdir(parents=True, exist_ok=True)

    by_chapter: dict[str, list[dict[str, Any]]] = {}
    for ch in chapters:
        cid = ch["chapter_id"]
        raw = ch.get("raw") or {}
        refs = raw.get("image_refs") or []
        rel_list: list[dict[str, Any]] = []
        for ref in refs:
            src = Path(ref["path"])
            if not src.exists():
                logger.warning("image not found, skipping: %s", src)
                continue
            dest = out_images / src.name
            try:
                shutil.copyfile(src, dest)
            except OSError as e:
                logger.warning("image copy failed %s -> %s: %s", src, dest, e)
                continue
            rel_list.append({
                "id": ref.get("id") or src.stem,
                "rel": f"images/{dest.name}",
                "page": ref.get("page"),
            })
        by_chapter[cid] = rel_list
    return by_chapter


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


def _figures_section(images: list[dict[str, Any]]) -> str:
    if not images:
        return ""
    items: list[str] = []
    for img in images:
        cap = f"p.{_esc(img['page'])}" if img.get("page") is not None else ""
        items.append(
            '<figure>'
            f'<img src="{_esc(img["rel"])}" alt="{_esc(img["id"])}" loading="lazy">'
            f'<figcaption>{cap}</figcaption>'
            '</figure>'
        )
    return (
        '<section id="figures">'
        '<h2>그림</h2>'
        '<div class="figures">' + "".join(items) + "</div>"
        "</section>"
    )


def _summary_section(summary: dict[str, Any]) -> str:
    text = summary.get("summary") or ""
    key_points = summary.get("key_points") or []
    parts = ['<section id="summary"><h2>요약</h2>']
    # 단락 분리
    for para in str(text).split("\n\n"):
        para = para.strip()
        if para:
            parts.append(f"<p>{_esc(para)}</p>")
    parts.append("</section>")

    if key_points:
        parts.append('<section id="key-points"><h2>핵심 포인트</h2><ul>')
        for kp in key_points:
            parts.append(f"<li>{_esc(kp)}</li>")
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
    images: list[dict[str, Any]],
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
        _figures_section(images),
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

def _page_shell(
    *,
    lang: str,
    title: str,
    body: str,
    page_kind: str,
    chapter_id: str | None = None,
) -> str:
    data_attrs = f'data-page="{_esc(page_kind)}"'
    if chapter_id:
        data_attrs += f' data-chapter-id="{_esc(chapter_id)}"'
    return (
        f'<!DOCTYPE html><html lang="{_esc(lang)}"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_esc(title)}</title>'
        '<link rel="stylesheet" href="assets/style.css">'
        '</head><body>'
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
            f'<span class="chapter-title">{_esc(title)}</span>'
            f'<span class="chapter-range">p.{_esc(pr[0])}–{_esc(pr[1])}</span>'
            "</div>"
            '<div class="progress-bar"><i></i></div>'
            '<div class="progress-text">아직 학습하지 않음</div>'
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
        images_by_chapter = _copy_chapter_images(chapters, output_dir)

        single = len(chapters) == 1
        book_title = book_info.get("title") or "Study"

        if single:
            ch = chapters[0]
            body = _book_info_header(book_info) + (
                '<article>' + _chapter_body(ch, images_by_chapter.get(ch["chapter_id"], []), opts) + '</article>'
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
            # 각 챕터 페이지
            for i, ch in enumerate(chapters):
                cid = ch["chapter_id"]
                ch_title = (ch.get("summary") or {}).get("title") or ch["meta"].get("title") or cid
                images = images_by_chapter.get(cid, [])
                article_body = (
                    _chapter_nav_links(chapters, i, show_index_link=True)
                    + '<article>' + _chapter_body(ch, images, opts) + '</article>'
                    + _chapter_nav_links(chapters, i, show_index_link=True)
                )
                (output_dir / f"{cid}.html").write_text(
                    _page_shell(
                        lang=language,
                        title=f"{book_title} · {ch_title}",
                        body=article_body,
                        page_kind="chapter",
                        chapter_id=cid,
                    ),
                    encoding="utf-8",
                )

        # progress 폴더 자리만 만들어 둠 (serve.py가 첫 GET 시 생성하기도 함)
        (output_dir / "progress").mkdir(exist_ok=True)
