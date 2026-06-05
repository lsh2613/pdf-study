"""HtmlRenderer + 사이드바 + 완료 토글 + 옵션 비활성 섹션 검증."""
from __future__ import annotations

from pdf_study import server


def _fake_summary(cid: str, *, mc=True, sa=True, rf=True):
    questions = {
        "multiple_choice": [
            {"id": f"{cid}_mc", "question": "?",
             "options": ["A", "B"], "answer_index": 0, "explanation": ""}
        ] if mc else [],
        "short_answer": [
            {"id": f"{cid}_sa", "question": "?", "model_answer": "ans"}
        ] if sa else [],
        "reflection": [
            {"id": f"{cid}_rf", "question": "?", "model_answer": "ans"}
        ] if rf else [],
    }
    return {
        "chapter_id": cid, "title": f"제목 {cid}",
        "summary": "본문 요약 내용입니다.", "key_points": ["p1", "p2"],
        "questions": questions,
    }


def _build_multi(ko_with_toc, tmp_path, *, opts=None):
    """ko_with_toc.pdf 기반으로 multi-chapter site를 만들어 output_dir 반환."""
    opts = {"execution_mode": "sequential", "extraction_mode": "text", **(opts or {})}
    r = server.init_work(str(ko_with_toc), str(tmp_path / "out"), **opts)
    wid = r["data"]["work_id"]
    s = server.scan_pdf(wid)
    chs = s["data"]["recommendations"]["suggested_chapters"]
    server.set_chapters(wid, chs, {"title": "테스트용 한국어 책", "author": "T"})
    for c in chs:
        cid = c["chapter_id"]
        server.save_chapter_result(wid, cid, _fake_summary(cid))
        if server.get_subagent_prompts(wid)["data"]["enabled_types"]["extension"]:
            server.save_extension_result(wid, cid, {
                "chapter_id": cid,
                "questions": {"extension": [
                    {"id": f"{cid}_ex", "question": "?",
                     "context": "ctx", "model_answer": "ans",
                     "sources": ["https://e.com/"]}
                ]},
            })
    fin = server.finalize_study(wid, "html")
    assert fin["ok"], fin
    return wid, tmp_path / "out", chs


def _build_single(ko_short, tmp_path, *, opts=None):
    opts = {"execution_mode": "sequential", "extraction_mode": "text", **(opts or {})}
    r = server.init_work(str(ko_short), str(tmp_path / "out"), **opts)
    wid = r["data"]["work_id"]
    server.scan_pdf(wid)
    server.set_chapters(wid, [
        {"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}
    ])
    server.save_chapter_result(wid, "ch1", _fake_summary("ch1"))
    if server.get_subagent_prompts(wid)["data"]["enabled_types"]["extension"]:
        server.save_extension_result(wid, "ch1", {
            "chapter_id": "ch1", "questions": {"extension": []}
        })
    fin = server.finalize_study(wid, "html")
    assert fin["ok"], fin
    return wid, tmp_path / "out"


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


def test_html_lang_reflects_state_language(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    html = (out / "ch1.html").read_text(encoding="utf-8")
    assert '<html lang="ko"' in html


def test_assets_are_copied(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    for f in ("assets/style.css", "assets/storage.js", "assets/grading.js"):
        assert (out / f).exists(), f
    for f in ("serve.py", "README.md"):
        assert (out / f).exists(), f


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
