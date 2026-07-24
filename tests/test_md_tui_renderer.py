"""MdTuiRenderer: 챕터별 폴더 + summary.md + quiz.json + launcher + 옵션 필터 검증.

TUI 엔진(study_tui.py)은 대화형이라 직접 실행하지 않고,
순수 헬퍼(_quiz_data 채점 데이터, _chapter_sort_key)와 산출물 구조만 검증한다.
"""
from __future__ import annotations

import json

from pdf_study import server
from pdf_study.renderer.md_tui_renderer import _book_md, _summary_md
from .conftest import build_rendered_study


def _build_multi(ko_with_toc, tmp_path, *, opts=None):
    _, out, chapters = build_rendered_study(
        ko_with_toc,
        tmp_path,
        "md_tui",
        options=opts,
        summary_kwargs={"answer_index": 1, "question": "Q?", "model_answer": "정답"},
    )
    return out, chapters


def test_markdown_labels_pdf_and_source_pages():
    chapter = {
        "chapter_id": "ch1",
        "meta": {
            "title": "본문",
            "pdf_pages": [19, 23],
            "source_pages": [1, 5],
        },
        "summary": {"summary": "요약", "key_points": []},
    }

    assert "PDF p.19–23 · 원문 p.1–5" in _book_md({}, [chapter], page_offset=18)
    assert "PDF p.19–23 · 원문 p.1–5" in _summary_md(chapter, page_offset=18)


def test_markdown_distinguishes_unknown_and_absent_source_pages():
    chapter = {
        "chapter_id": "ch1",
        "meta": {"title": "서문", "pdf_pages": [1, 3], "source_pages": None},
        "summary": {"summary": "요약", "key_points": []},
    }

    assert "원문 페이지 미상" in _summary_md(chapter, page_offset=None)
    assert "원문 페이지 없음" in _summary_md(chapter, page_offset=18)


def test_per_chapter_folders_and_files(ko_with_toc, tmp_path):
    out, chs = _build_multi(ko_with_toc, tmp_path)
    assert (out / "study_tui.py").exists()
    assert (out / "book.md").exists()
    assert (out / "README.md").exists()
    for c in chs:
        cid = c["chapter_id"]
        assert (out / cid / "summary.md").exists()
        assert (out / cid / "quiz.json").exists()
        assert (out / cid / "study_tui.py").exists()


def test_book_md_links_chapters(ko_with_toc, tmp_path):
    out, chs = _build_multi(ko_with_toc, tmp_path)
    book = (out / "book.md").read_text(encoding="utf-8")
    for c in chs:
        assert f"{c['chapter_id']}/summary.md" in book


def test_summary_md_has_key_points_not_questions(ko_with_toc, tmp_path):
    out, _ = _build_multi(ko_with_toc, tmp_path)
    md = (out / "ch1" / "summary.md").read_text(encoding="utf-8")
    assert "핵심 포인트" in md
    assert "본문 요약 내용입니다." in md
    assert "PDF p." in md
    assert "원문" in md
    # 문제/정답은 summary.md가 아니라 quiz.json에만
    assert "answer_index" not in md
    assert "model_answer" not in md


def test_quiz_json_merges_extension_and_questions(ko_with_toc, tmp_path):
    out, _ = _build_multi(ko_with_toc, tmp_path)
    quiz = json.loads((out / "ch1" / "quiz.json").read_text(encoding="utf-8"))
    q = quiz["questions"]
    assert q["multiple_choice"][0]["answer_index"] == 1
    assert q["short_answer"][0]["model_answer"] == "정답"
    assert q["reflection"]
    assert q["extension"][0] == {
        "id": "ch1_ex", "question": "Q?", "model_answer": "정답",
    }


def test_disabled_types_omitted_from_quiz(ko_with_toc, tmp_path):
    out, _ = _build_multi(
        ko_with_toc, tmp_path,
        opts={"enable_reflection": False, "enable_extension": False},
    )
    quiz = json.loads((out / "ch1" / "quiz.json").read_text(encoding="utf-8"))
    q = quiz["questions"]
    assert "multiple_choice" in q
    assert "short_answer" in q
    assert "reflection" not in q
    assert "extension" not in q


def test_chapter_launcher_calls_engine(ko_with_toc, tmp_path):
    out, _ = _build_multi(ko_with_toc, tmp_path)
    shim = (out / "ch1" / "study_tui.py").read_text(encoding="utf-8")
    assert "from study_tui import run_chapter" in shim
    assert "run_chapter(_here)" in shim


def test_progress_fingerprint_preserves_same_md_tui_generation(ko_with_toc, tmp_path):
    out, _ = _build_multi(ko_with_toc, tmp_path)
    state = json.loads((out / ".work" / "state.json").read_text(encoding="utf-8"))
    wid = state["work_id"]
    progress = out / "ch1" / "progress.json"
    progress.write_text('{"completed":true}', encoding="utf-8")

    rerendered = server._finalize_study_impl(wid, "md_tui")

    assert rerendered["ok"], rerendered
    assert progress.read_text(encoding="utf-8") == '{"completed":true}'
    assert (out / ".pdf-study-manifest.json").exists()


# --- rich 미설치 평문 폴백 셰임 --------------------------------------------

def _load_study_tui():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "templates" / "md_tui" / "study_tui.py"
    spec = importlib.util.spec_from_file_location("study_tui_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_plain_console_shims_run_without_rich(monkeypatch, capsys):
    """rich 없이도 동작하도록 평문 셰임이 마크업 제거 + 입력 처리를 한다."""
    m = _load_study_tui()
    console, Markdown, Panel, Prompt, Confirm = m._plain_console_shims()

    # 인라인 마크업 태그 제거
    console.print("[bold]제목[/bold] 본문")
    out = capsys.readouterr().out
    assert "제목 본문" in out and "[bold]" not in out

    # Markdown / Panel 평문 출력
    console.print(Markdown("# 헤더\n내용"))
    console.print(Panel("패널본문", title="해설"))
    out = capsys.readouterr().out
    assert "헤더" in out and "패널본문" in out and "해설" in out

    # Prompt.ask: 잘못된 입력은 재요청, choices 검증
    inputs = iter(["x", "2"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    assert Prompt.ask("선택", choices=["1", "2"]) == "2"

    # Confirm.ask: y/빈입력(default)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    assert Confirm.ask("진행?", default=False) is True
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert Confirm.ask("진행?", default=True) is True


def test_plain_mode_run_chapter_end_to_end(tmp_path, monkeypatch, capsys):
    """rich 셰임으로 실제 엔진(run_chapter)이 끝까지 도는지 검증 (요약 읽기→종료)."""
    m = _load_study_tui()
    console, Markdown, Panel, Prompt, Confirm = m._plain_console_shims()
    for name, obj in (("console", console), ("Markdown", Markdown),
                      ("Panel", Panel), ("Prompt", Prompt), ("Confirm", Confirm)):
        monkeypatch.setattr(m, name, obj)

    ch = tmp_path / "ch1"
    ch.mkdir()
    (ch / "summary.md").write_text("# 제목\n본문내용", encoding="utf-8")
    (ch / "quiz.json").write_text('{"title":"T","questions":{}}', encoding="utf-8")

    inputs = iter(["r", "q"])  # 요약 읽기 → 종료
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    m.run_chapter(ch)  # 예외 없이 종료해야 함
    assert "본문내용" in capsys.readouterr().out


def test_in_progress_chapter_resumes_without_showing_menu(tmp_path, monkeypatch):
    """저장된 답안이 있으면 챕터 메뉴를 묻지 않고 문제 풀이를 바로 재개한다."""
    m = _load_study_tui()
    ch = tmp_path / "ch1"
    ch.mkdir()
    (ch / "quiz.json").write_text(
        '{"title":"T","questions":{"multiple_choice":['
        '{"id":"q1","question":"Q1","options":["a","b"],"answer_index":0},'
        '{"id":"q2","question":"Q2","options":["a","b"],"answer_index":0}]}}',
        encoding="utf-8",
    )
    (ch / "progress.json").write_text(
        '{"answers":{"q1":{"selected":0,"correct":true}},"completed":false}',
        encoding="utf-8",
    )
    resumed = []
    monkeypatch.setattr(m, "_run_quiz", lambda chapter_dir, quiz, prog: resumed.append(prog))
    monkeypatch.setattr(
        m.Prompt,
        "ask",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("menu must not be shown")),
    )

    m.run_chapter(ch)

    assert resumed and resumed[0]["answers"] == {"q1": {"selected": 0, "correct": True}}


def test_run_quiz_skips_answers_already_saved(tmp_path, monkeypatch):
    """이어하기는 저장된 문제를 다시 묻지 않고 첫 미응답 문제부터 처리한다."""
    m = _load_study_tui()
    ch = tmp_path / "ch1"
    ch.mkdir()
    quiz = {
        "questions": {
            "multiple_choice": [
                {"id": "q1", "question": "Q1", "options": ["a", "b"], "answer_index": 0},
                {"id": "q2", "question": "Q2", "options": ["a", "b"], "answer_index": 0},
            ],
        },
    }
    asked = []
    monkeypatch.setattr(m, "_ask_mc", lambda q: asked.append(q["id"]) or {"selected": 0, "correct": True})
    monkeypatch.setattr(m.Confirm, "ask", lambda *args, **kwargs: False)

    m._run_quiz(ch, quiz, {"answers": {"q1": {"selected": 0, "correct": True}}, "completed": False})

    assert asked == ["q2"]
