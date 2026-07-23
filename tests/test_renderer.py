"""HtmlRenderer + 사이드바 + 완료 토글 + 옵션 비활성 섹션 검증."""
from __future__ import annotations

import copy
import stat
import sys

import pytest

from pdf_study import question_contract, server
from pdf_study.renderer.html_renderer import (
    HtmlRenderer,
    _FallbackMd,
    _chapter_body,
    _summary_section,
)
from pdf_study.renderer.study_loader import _unescape_if_double_escaped


def _scan(wid):
    options = server.workspace.load_state(wid)["question_options"]
    return server.scan_pdf(
        wid,
        enable_short_answer=True if options.get("short_answer") is None else None,
        enable_reflection=True if options.get("reflection") is None else None,
        enable_extension=True if options.get("extension") is None else None,
    )


@pytest.fixture(autouse=True)
def stub_scan_toc_ocr(monkeypatch):
    """scan_pdf 목차 OCR 테스트가 실제 PaddleOCR 모델을 로드하지 않게 한다."""
    class StubWorker:
        def process_image(self, img_path):
            return "목차 OCR 텍스트"

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda: StubWorker())


def _fake_summary(cid: str, *, mc=True, sa=True, rf=True):
    result = copy.deepcopy(question_contract.summary_payload_example())
    result.update(
        chapter_id=cid, title=f"제목 {cid}", summary="본문 요약 내용입니다.",
        key_points=["p1", "p2"],
    )
    questions = result["questions"]
    questions["multiple_choice"][0].update(
        id=f"{cid}_mc", question="?", options=["A", "B"], answer_index=0,
        explanation="해설",
    )
    questions["short_answer"][0].update(id=f"{cid}_sa", question="?", model_answer="ans")
    questions["reflection"][0].update(id=f"{cid}_rf", question="?", model_answer="ans")
    if not mc:
        questions["multiple_choice"] = []
    if not sa:
        questions["short_answer"] = []
    if not rf:
        questions["reflection"] = []
    return result


def _fake_extension(cid: str):
    result = copy.deepcopy(question_contract.extension_payload_example())
    result["chapter_id"] = cid
    result["questions"]["extension"][0].update(
        id=f"{cid}_ex", question="?", model_answer="ans",
    )
    return result


def _build_multi(ko_with_toc, tmp_path, *, opts=None):
    """ko_with_toc.pdf 기반으로 multi-chapter site를 만들어 output_dir 반환."""
    opts = opts or {}
    r = server.init_work(str(ko_with_toc), str(tmp_path / "out"), **opts)
    wid = r["data"]["work_id"]
    s = _scan(wid)
    chs = s["data"]["recommendations"]["suggested_chapters"]
    server.set_chapters(wid, chs, execution_mode="sequential", extraction_mode="text",
                        book_info={"title": "테스트용 한국어 책", "author": "T"})
    for c in chs:
        cid = c["chapter_id"]
        server.save_chapter_result(wid, cid, _fake_summary(cid))
        if server.get_subagent_prompts(wid)["data"]["enabled_types"]["extension"]:
            server.save_extension_result(wid, cid, _fake_extension(cid))
    fin = server.finalize_study(wid, "html")
    assert fin["ok"], fin
    return wid, tmp_path / "out", chs


def _build_single(ko_short, tmp_path, *, opts=None):
    opts = opts or {}
    r = server.init_work(str(ko_short), str(tmp_path / "out"), **opts)
    wid = r["data"]["work_id"]
    _scan(wid)
    server.set_chapters(wid, [
        {"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}
    ], execution_mode="sequential", extraction_mode="text")
    server.save_chapter_result(wid, "ch1", _fake_summary("ch1"))
    if server.get_subagent_prompts(wid)["data"]["enabled_types"]["extension"]:
        server.save_extension_result(wid, "ch1", _fake_extension("ch1"))
    fin = server.finalize_study(wid, "html")
    assert fin["ok"], fin
    return wid, tmp_path / "out"


def test_chapter_body_labels_pdf_and_source_pages():
    chapter = {
        "chapter_id": "ch1",
        "meta": {
            "title": "본문",
            "pdf_pages": [19, 23],
            "source_pages": [1, 5],
        },
        "summary": {"summary": "요약", "questions": {}},
    }

    body = _chapter_body(chapter, {}, page_offset=18)

    assert "PDF p.19–23 · 원문 p.1–5" in body


def test_chapter_body_distinguishes_unknown_and_absent_source_pages():
    chapter = {
        "chapter_id": "ch1",
        "meta": {"title": "서문", "pdf_pages": [1, 3], "source_pages": None},
        "summary": {"summary": "요약", "questions": {}},
    }

    unknown = _chapter_body(chapter, {}, page_offset=None)
    absent = _chapter_body(chapter, {}, page_offset=18)

    assert "PDF p.1–3 · 원문 페이지 미상" in unknown
    assert "PDF p.1–3 · 원문 페이지 없음" in absent


# ---------------------------- 멀티 챕터 ----------------------------

def test_multi_chapter_generates_index_and_chapter_pages(ko_with_toc, tmp_path):
    _, out, chs = _build_multi(ko_with_toc, tmp_path)
    assert (out / "index.html").exists()
    for c in chs:
        assert (out / f"{c['chapter_id']}.html").exists()
    assert not (out / "main.html").exists()


def test_chapter_page_has_sidebar_with_current_active(ko_with_toc, tmp_path):
    _, out, chs = _build_multi(ko_with_toc, tmp_path)
    html = (out / "ch2.html").read_text(encoding="utf-8")
    assert 'class="has-sidebar"' in html
    assert '<aside class="sidebar"' in html
    # is-active는 자기 챕터만
    assert html.count("sidebar-link is-active") == 1
    assert 'sidebar-link is-active" data-chapter="ch2"' in html
    # 모든 챕터가 사이드바 링크로 등장
    for c in chs:
        assert f'data-chapter="{c["chapter_id"]}"' in html


def test_index_page_has_no_sidebar(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    idx = (out / "index.html").read_text(encoding="utf-8")
    assert '<aside class="sidebar"' not in idx
    assert 'has-sidebar' not in idx
    # 챕터 카드에 체크 자리 + status 텍스트
    assert 'class="chapter-check"' in idx
    assert 'class="status-text"' in idx
    assert "PDF p." in idx
    assert "원문" in idx
    # 진행률 바 흔적 없음
    assert "progress-bar" not in idx
    assert "progress-text" not in idx


def test_chapter_page_has_complete_button(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    html = (out / "ch1.html").read_text(encoding="utf-8")
    assert 'class="completion-control"' in html
    assert 'class="complete-btn"' in html
    assert 'aria-pressed="false"' in html


def test_disabled_question_types_omit_sections(ko_with_toc, tmp_path):
    """reflection / extension 비활성이면 해당 섹션이 페이지에 없어야 한다."""
    _, out, _ = _build_multi(
        ko_with_toc, tmp_path,
        opts={"enable_reflection": False, "enable_extension": False},
    )
    html = (out / "ch1.html").read_text(encoding="utf-8")
    assert 'id="mc"' in html
    assert 'id="sa"' in html
    assert 'id="rf"' not in html
    assert 'id="ex"' not in html


def test_html_uses_fixed_korean_document_language(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    html = (out / "ch1.html").read_text(encoding="utf-8")
    assert '<html lang="ko"' in html


def test_assets_are_copied(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    for f in ("assets/style.css", "assets/storage.js"):
        assert (out / f).exists(), f
    assert not (out / "assets/grading.js").exists()
    assert "assets/grading.js" not in (out / "ch1.html").read_text(encoding="utf-8")
    for f in ("study_html.py", "README.md"):
        assert (out / f).exists(), f


def test_html_output_contains_project_local_launch_scripts(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    sh = out / "start_study.sh"
    bat = out / "start_study.bat"

    assert sh.stat().st_mode & stat.S_IXUSR
    assert sys.executable in sh.read_text(encoding="utf-8")
    assert "study_html.py" in sh.read_text(encoding="utf-8")
    assert "--port 0" in sh.read_text(encoding="utf-8")
    assert "exec " in sh.read_text(encoding="utf-8")
    assert '"$@"' in sh.read_text(encoding="utf-8")
    assert sys.executable in bat.read_text(encoding="utf-8")
    assert '"%~dp0study_html.py" --port 0 %*' in bat.read_text(encoding="utf-8")


def test_storage_js_has_no_legacy_scroll_metrics(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    js = (out / "assets/storage.js").read_text(encoding="utf-8")
    # 진행률 측정은 명시적 완료 버튼만 — scroll-based 잔재 없음
    assert "computeScrollRatio" not in js
    assert "applyScrollProgress" not in js
    assert "reading_progress" not in js
    # 완료 토글 + 사이드바 라벨링은 있어야
    assert "setSidebarCompleted" in js
    assert "complete-btn" in js


def test_model_answer_reveal_button_toggles_open_and_closed(ko_with_toc, tmp_path):
    """모범답안 버튼은 다시 접을 수 있어야 하며 비활성화하지 않는다."""
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    js = (out / "assets/storage.js").read_text(encoding="utf-8")
    assert "const expanded = !answerBlock.hidden" in js
    assert "answerBlock.hidden = expanded" in js
    assert "모범답안 접기" in js
    assert "reveal.disabled = true" not in js

    html = (out / "ch1.html").read_text(encoding="utf-8")
    assert 'class="reveal" aria-expanded="false"' in html


def test_extension_question_uses_same_answer_ui_without_reference_block(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    html = (out / "ch1.html").read_text(encoding="utf-8")
    assert 'id="ex"' in html
    assert "ch1_ex" in html
    assert '<button type="button" class="reveal"' in html


def test_force_ignores_stale_results_for_pending_chapter(ko_short, tmp_path):
    out = tmp_path / "out"
    init = server.init_work(
        str(ko_short),
        str(out),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    wid = init["data"]["work_id"]
    _scan(wid)
    set_result = server.set_chapters(
        wid,
        [{"chapter_id": "ch1", "title": "새 챕터", "pdf_pages": [1, 12]}],
        execution_mode="sequential",
        extraction_mode="text",
    )
    assert set_result["ok"], set_result
    (server.workspace.summaries_dir(wid) / "ch1.json").write_text(
        '{"chapter_id":"ch1","title":"예전 제목",'
        '"summary":"STALE_SUMMARY","key_points":["old"]}',
        encoding="utf-8",
    )
    (server.workspace.quiz_dir(wid) / "ch1.json").write_text(
        '{"questions":{"multiple_choice":[{"id":"old",'
        '"question":"STALE_QUESTION","options":["A","B"],'
        '"answer_index":0,"explanation":"old"}]}}',
        encoding="utf-8",
    )

    rendered = server.finalize_study(wid, "html", force=True)

    assert rendered["ok"], rendered
    html = (out / "main.html").read_text(encoding="utf-8")
    assert "새 챕터" in html
    assert "STALE_SUMMARY" not in html
    assert "STALE_QUESTION" not in html


def test_managed_output_removes_pages_for_removed_chapters(ko_with_toc, tmp_path):
    wid, out, _ = _build_multi(ko_with_toc, tmp_path)
    assert (out / "ch2.html").exists()
    server.workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "남은 챕터", "pdf_pages": [5, 12]},
    ])
    saved = server.save_chapter_result(wid, "ch1", _fake_summary("ch1"))
    assert saved["ok"], saved
    extension = server.save_extension_result(wid, "ch1", _fake_extension("ch1"))
    assert extension["ok"], extension

    rerendered = server.finalize_study(wid, "html")

    assert rerendered["ok"], rerendered
    assert (out / "main.html").exists()
    assert not (out / "index.html").exists()
    assert not (out / "ch1.html").exists()
    assert not (out / "ch2.html").exists()
    assert (out / ".pdf-study-manifest.json").exists()


def test_managed_output_switches_format_without_touching_unrelated_file(
    ko_with_toc, tmp_path
):
    wid, out, _ = _build_multi(ko_with_toc, tmp_path)
    unrelated = out / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    switched = server.finalize_study(wid, "md_tui")

    assert switched["ok"], switched
    assert (out / "book.md").exists()
    assert (out / "ch1" / "summary.md").exists()
    assert not (out / "index.html").exists()
    assert not (out / "ch1.html").exists()
    assert not (out / "assets").exists()
    assert not (out / "study_html.py").exists()
    assert unrelated.read_text(encoding="utf-8") == "keep me"


def test_progress_fingerprint_preserves_same_html_generation(ko_with_toc, tmp_path):
    wid, out, _ = _build_multi(ko_with_toc, tmp_path)
    progress = out / "progress" / "ch1.json"
    progress.write_text('{"completed":true}', encoding="utf-8")

    rerendered = server.finalize_study(wid, "html")

    assert rerendered["ok"], rerendered
    assert progress.read_text(encoding="utf-8") == '{"completed":true}'
    manifest = (out / ".pdf-study-manifest.json").read_text(encoding="utf-8")
    assert '"study_fingerprint"' in manifest


def test_progress_fingerprint_resets_after_content_change(ko_with_toc, tmp_path):
    wid, out, _ = _build_multi(ko_with_toc, tmp_path)
    progress = out / "progress" / "ch1.json"
    progress.write_text('{"completed":true}', encoding="utf-8")
    changed = _fake_summary("ch1")
    changed["summary"] = "새로 생성한 요약"
    saved = server.save_chapter_result(wid, "ch1", changed)
    assert saved["ok"], saved

    rerendered = server.finalize_study(wid, "html")

    assert rerendered["ok"], rerendered
    assert not progress.exists()


def test_render_rollback_keeps_previous_generation(
    ko_with_toc, tmp_path, monkeypatch
):
    wid, out, _ = _build_multi(ko_with_toc, tmp_path)
    index_before = (out / "index.html").read_bytes()
    manifest_path = out / ".pdf-study-manifest.json"
    manifest_before = manifest_path.read_bytes()

    def fail_render(self, work_id, output_dir):
        (output_dir / "partial.html").write_text("partial", encoding="utf-8")
        raise RuntimeError("render failed")

    monkeypatch.setattr(HtmlRenderer, "render", fail_render)

    failed = server.finalize_study(wid, "html")

    assert failed["ok"] is False
    assert (out / "index.html").read_bytes() == index_before
    assert manifest_path.read_bytes() == manifest_before
    assert not (out / "partial.html").exists()


def test_managed_output_refuses_to_overwrite_unmanaged_name(ko_short, tmp_path):
    out = tmp_path / "out"
    init = server.init_work(
        str(ko_short),
        str(out),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    wid = init["data"]["work_id"]
    _scan(wid)
    set_result = server.set_chapters(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}],
        execution_mode="sequential",
        extraction_mode="text",
    )
    assert set_result["ok"], set_result
    saved = server.save_chapter_result(wid, "ch1", _fake_summary("ch1"))
    assert saved["ok"], saved
    user_readme = out / "README.md"
    user_readme.write_text("user-owned", encoding="utf-8")

    rendered = server.finalize_study(wid, "html")

    assert rendered["ok"] is False
    assert "unmanaged paths" in rendered["error"]
    assert user_readme.read_text(encoding="utf-8") == "user-owned"
    assert not (out / ".pdf-study-manifest.json").exists()


def test_managed_output_refuses_to_overwrite_unmanaged_broken_symlink(
    ko_short, tmp_path
):
    out = tmp_path / "out"
    init = server.init_work(
        str(ko_short),
        str(out),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    wid = init["data"]["work_id"]
    _scan(wid)
    set_result = server.set_chapters(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}],
        execution_mode="sequential",
        extraction_mode="text",
    )
    assert set_result["ok"], set_result
    saved = server.save_chapter_result(wid, "ch1", _fake_summary("ch1"))
    assert saved["ok"], saved
    user_readme = out / "README.md"
    user_readme.symlink_to(out / "missing-user-readme")

    rendered = server.finalize_study(wid, "html")

    assert rendered["ok"] is False
    assert "unmanaged paths" in rendered["error"]
    assert user_readme.is_symlink()
    assert not (out / ".pdf-study-manifest.json").exists()


# ---------------------------- 단일 챕터 ----------------------------

def test_single_chapter_emits_main_html_only(ko_short, tmp_path):
    _, out = _build_single(ko_short, tmp_path)
    assert (out / "main.html").exists()
    assert not (out / "index.html").exists()


def test_single_chapter_has_no_sidebar(ko_short, tmp_path):
    _, out = _build_single(ko_short, tmp_path)
    mh = (out / "main.html").read_text(encoding="utf-8")
    assert '<aside class="sidebar"' not in mh
    assert "has-sidebar" not in mh


def test_single_chapter_still_has_complete_button(ko_short, tmp_path):
    _, out = _build_single(ko_short, tmp_path)
    mh = (out / "main.html").read_text(encoding="utf-8")
    assert 'class="complete-btn"' in mh


def test_complete_button_is_floating_fixed(ko_with_toc, tmp_path):
    """완료 버튼이 스크롤과 무관하게 보이도록 .completion-control이 fixed여야 한다."""
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    css = (out / "assets" / "style.css").read_text(encoding="utf-8")
    # .completion-control 블록이 position: fixed 를 포함
    block = css.split(".completion-control", 1)[1].split("}", 1)[0]
    assert "position: fixed" in block


# ---------------------------- 요약 마크다운 (그림 없음) ----------------------------

def test_summary_markdown_is_rendered_not_escaped():
    summary = {
        "summary": "## 개요\n\n트랜잭션은 **원자성**을 보장하고 `SERIALIZABLE`을 쓴다.\n\n"
                   "| 수준 | 비용 |\n|---|---|\n| 높음 | 큼 |",
        "key_points": ["**ACID** 4요소"],
    }
    html = _summary_section(summary)
    assert "<strong>원자성</strong>" in html
    assert "<code>SERIALIZABLE</code>" in html
    assert "<table>" in html and "<td>높음</td>" in html
    # 마크다운 원문(별표/해시)이 그대로 노출되지 않아야
    assert "**원자성**" not in html
    # 핵심 포인트도 인라인 마크다운 렌더
    assert "<strong>ACID</strong>" in html


def test_summary_headings_demoted_under_section():
    """본문 ## 헤딩은 섹션 제목(h2) 아래 h3로 낮춰야 계층이 깔끔."""
    html = _summary_section({"summary": "## 개요\n내용", "key_points": []})
    assert "<h2>요약</h2>" in html       # 섹션 제목은 h2 유지
    assert "<h3>개요</h3>" in html       # 본문 ##(h2) → h3
    assert "<h2>개요</h2>" not in html


def test_double_escaped_newlines_recovered():
    r"""summary가 진짜 개행 없이 리터럴 `\n`만 가지면 복구해 마크다운이 살아난다."""
    broken = "03. 사용자\\n\\n### 1. 식별\\n**호스트**를 묶는다.\\n- 글로벌\\n- DB"
    fixed = _unescape_if_double_escaped(broken)
    html = _summary_section({"summary": fixed, "key_points": []})
    assert "<h4>1. 식별</h4>" in html        # ### → h3 → 섹션 아래로 demote → h4
    assert "<strong>호스트</strong>" in html
    assert "<li>글로벌</li>" in html
    assert "\\n" not in html                   # 리터럴 역슬래시-n 누출 없음


def test_normal_summary_newlines_untouched():
    r"""진짜 개행이 있는 정상 요약(코드블록 내 \n 포함)은 변환하지 않는다."""
    normal = "본문\n\n```py\nprint('a\\nb')\n```"
    assert _unescape_if_double_escaped(normal) == normal
    assert _unescape_if_double_escaped("그냥 한 줄") == "그냥 한 줄"


def test_fallback_md_renders_without_markdown_it():
    """markdown-it-py가 없어도 내장 폴백이 마크다운을 HTML로 변환(raw 노출 방지)."""
    fb = _FallbackMd()
    out = fb.render(
        "## 제목\n본문 **굵게** 와 `코드`.\n- 항목1\n- 항목2\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n```py\nx = 1\n```"
    )
    assert "<h2>제목</h2>" in out
    assert "<strong>굵게</strong>" in out and "<code>코드</code>" in out
    assert "<ul>" in out and "<li>항목1</li>" in out
    assert "<table>" in out and "<td>1</td>" in out
    assert "<pre><code" in out and "x = 1" in out
    # 마크다운 원문이 그대로 새어나오지 않아야
    assert "**굵게**" not in out and "## 제목" not in out
    # 인라인 전용 진입점
    assert fb.renderInline("`x` **y**") == "<code>x</code> <strong>y</strong>"


def test_no_figures_section_rendered():
    """그림 기능 제거: 챕터 본문 어디에도 '그림' 섹션이 생기지 않는다."""
    summary = {"summary": "## 개요\n내용", "key_points": ["p1"]}
    html = _summary_section(summary)
    assert 'id="figures"' not in html
    # 헬퍼는 이제 (html, used) 튜플이 아니라 문자열만 반환
    assert isinstance(html, str)


def test_chapter_page_has_no_figures_section(ko_with_toc, tmp_path):
    """전체 파이프라인에서도 챕터 페이지에 '그림' 섹션이 없어야 한다."""
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    html = (out / "ch1.html").read_text(encoding="utf-8")
    assert 'id="figures"' not in html
    assert "<h2>그림</h2>" not in html
