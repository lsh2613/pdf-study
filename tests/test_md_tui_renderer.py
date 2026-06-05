"""MdTuiRenderer: 챕터별 폴더 + summary.md + quiz.json + launcher + 옵션 필터 검증.

TUI 엔진(study.py)은 대화형이라 직접 실행하지 않고,
순수 헬퍼(_quiz_data 채점 데이터, _chapter_sort_key)와 산출물 구조만 검증한다.
"""
from __future__ import annotations

import json

from pdf_study import server


def _fake_summary(cid: str, *, mc=True, sa=True, rf=True):
    questions = {
        "multiple_choice": [
            {"id": f"{cid}_mc", "question": "Q?",
             "options": ["A", "B"], "answer_index": 1, "explanation": "왜냐하면"}
        ] if mc else [],
        "short_answer": [
            {"id": f"{cid}_sa", "question": "Q?", "model_answer": "정답"}
        ] if sa else [],
        "reflection": [
            {"id": f"{cid}_rf", "question": "Q?", "model_answer": "정답"}
        ] if rf else [],
    }
    return {
        "chapter_id": cid, "title": f"제목 {cid}",
        "summary": "본문 요약 내용입니다.", "key_points": ["p1", "p2"],
        "questions": questions,
    }


def _build_multi(ko_with_toc, tmp_path, *, opts=None):
    opts = {"execution_mode": "sequential", "extraction_mode": "text", **(opts or {})}
    r = server.init_work(str(ko_with_toc), str(tmp_path / "out"), **opts)
    wid = r["data"]["work_id"]
    s = server.scan_pdf(wid)
    chs = s["data"]["recommendations"]["suggested_chapters"]
    server.set_chapters(wid, chs, {"title": "테스트 책", "author": "T"})
    for c in chs:
        cid = c["chapter_id"]
        server.save_chapter_result(wid, cid, _fake_summary(cid))
        if server.get_subagent_prompts(wid)["data"]["enabled_types"]["extension"]:
            server.save_extension_result(wid, cid, {
                "chapter_id": cid,
                "questions": {"extension": [
                    {"id": f"{cid}_ex", "question": "Q?",
                     "context": "ctx", "model_answer": "정답",
                     "sources": ["https://e.com/"]}
                ]},
            })
    fin = server.finalize_study(wid, "md_tui")
    assert fin["ok"], fin
    return tmp_path / "out", chs


def test_per_chapter_folders_and_files(ko_with_toc, tmp_path):
    out, chs = _build_multi(ko_with_toc, tmp_path)
    assert (out / "study.py").exists()
    assert (out / "book.md").exists()
    assert (out / "README.md").exists()
    for c in chs:
        cid = c["chapter_id"]
        assert (out / cid / "summary.md").exists()
        assert (out / cid / "quiz.json").exists()
        assert (out / cid / "study.py").exists()


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
    assert q["extension"][0]["sources"] == ["https://e.com/"]


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
    shim = (out / "ch1" / "study.py").read_text(encoding="utf-8")
    assert "from study import run_chapter" in shim
    assert "run_chapter(_here)" in shim
