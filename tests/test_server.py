"""server.py 11개 도구 in-process 흐름 테스트.

각 도구의 응답 형식 통일 + 주요 분기 검증.
"""
from __future__ import annotations

import pytest

from pdf_study import server, workspace


def _check_envelope(resp: dict) -> None:
    """모든 도구는 {ok, error, data, next_action} 형태로 응답해야 한다."""
    assert set(resp.keys()) == {"ok", "error", "data", "next_action"}


def test_init_work_rejects_all_disabled(tmp_path, ko_short):
    r = server.init_work(
        str(ko_short), str(tmp_path / "out"),
        enable_multiple_choice=False, enable_short_answer=False,
        enable_reflection=False, enable_extension=False,
    )
    _check_envelope(r)
    assert r["ok"] is False
    assert "question type" in r["error"]


def test_init_work_rejects_missing_pdf(tmp_path):
    r = server.init_work(str(tmp_path / "nope.pdf"), str(tmp_path / "out"))
    _check_envelope(r)
    assert r["ok"] is False


def test_scan_pdf_rejects_no_text_layer(tmp_path, scanned_empty):
    r0 = server.init_work(str(scanned_empty), str(tmp_path / "out"))
    assert r0["ok"]
    wid = r0["data"]["work_id"]
    r = server.scan_pdf(wid)
    _check_envelope(r)
    assert r["ok"] is False
    assert "ocrmypdf" in r["error"] or "OCR" in r["error"]


def test_get_chapter_content_for_unknown_chapter(tmp_path, ko_short):
    r0 = server.init_work(str(ko_short), str(tmp_path / "out"))
    wid = r0["data"]["work_id"]
    r = server.get_chapter_content(wid, "ch999")
    _check_envelope(r)
    assert r["ok"] is False
    assert "FileNotFoundError" in r["error"] or "not found" in r["error"]


def test_unknown_work_id_returns_ok_false(tmp_path):
    r = server.get_work_state("never-issued")
    _check_envelope(r)
    assert r["ok"] is False


def test_full_flow_save_and_finalize_html(tmp_path, ko_short):
    """init → scan → set → save → finalize 까지 의도된 응답 형식과 상태 전이."""
    r1 = server.init_work(str(ko_short), str(tmp_path / "out"))
    _check_envelope(r1); assert r1["ok"]
    wid = r1["data"]["work_id"]

    r2 = server.scan_pdf(wid)
    _check_envelope(r2); assert r2["ok"]
    chs = r2["data"]["recommendations"]["suggested_chapters"]
    assert chs and chs[0]["chapter_id"] == "ch1"

    r3 = server.set_chapters(wid, chs, {"title": "T", "author": "A"})
    _check_envelope(r3); assert r3["ok"]
    assert r3["data"]["chapter_count"] == 1

    # 본문 가져오기
    r4 = server.get_chapter_content(wid, "ch1")
    _check_envelope(r4); assert r4["ok"]
    assert r4["data"]["chapter_id"] == "ch1"
    assert r4["data"]["char_count"] > 0

    # 프롬프트
    r5 = server.get_subagent_prompts(wid)
    _check_envelope(r5); assert r5["ok"]
    assert r5["data"]["language"] == "ko"
    assert r5["data"]["chapter_ids"] == ["ch1"]
    assert "JSON 객체 하나만" in r5["data"]["summarizer_prompt"]

    # 가짜 결과 저장
    r6 = server.save_chapter_result(wid, "ch1", {
        "chapter_id": "ch1", "title": "전체",
        "summary": "요약", "key_points": ["p1"],
        "questions": {
            "multiple_choice": [
                {"id": "mc1", "question": "?",
                 "options": ["A","B"], "answer_index": 0, "explanation": ""}
            ],
            "short_answer": [], "reflection": [],
        },
    })
    _check_envelope(r6); assert r6["ok"]

    # extension 결과 저장 (옵션 켜져 있다는 가정 — 기본 enable_extension=True)
    r7 = server.save_extension_result(wid, "ch1", {
        "chapter_id": "ch1", "questions": {"extension": []}
    })
    _check_envelope(r7); assert r7["ok"]

    # 상태 조회
    r8 = server.get_work_state(wid)
    _check_envelope(r8); assert r8["ok"]
    entry = r8["data"]["chapters"]["ch1"]
    assert entry["summary_status"] == "completed"
    assert entry["extension_status"] == "completed"

    # pending 조회
    r9 = server.list_pending_chapters(wid)
    _check_envelope(r9); assert r9["ok"]
    assert r9["data"]["summary_pending"] == []
    assert r9["data"]["extension_enabled"] is True

    # finalize
    r10 = server.finalize_study(wid, output_format="html")
    _check_envelope(r10); assert r10["ok"], r10
    out = tmp_path / "out"
    # 단일 챕터 → main.html
    assert (out / "main.html").exists()
    assert not (out / "index.html").exists()


def test_finalize_rejects_unknown_format(tmp_path, ko_short):
    r1 = server.init_work(str(ko_short), str(tmp_path / "out"))
    wid = r1["data"]["work_id"]
    server.scan_pdf(wid)
    server.set_chapters(wid, [{"chapter_id":"ch1","title":"전체","page_range":[1,12]}])
    server.save_chapter_result(wid, "ch1", {
        "chapter_id":"ch1","summary":"","key_points":[],
        "questions":{"multiple_choice":[],"short_answer":[],"reflection":[]}
    })
    r = server.finalize_study(wid, output_format="bogus")
    _check_envelope(r)
    assert r["ok"] is False
    assert "output_format" in r["error"]


def test_md_tui_renderer_is_not_implemented(tmp_path, ko_short):
    r1 = server.init_work(str(ko_short), str(tmp_path / "out"))
    wid = r1["data"]["work_id"]
    server.scan_pdf(wid)
    server.set_chapters(wid, [{"chapter_id":"ch1","title":"전체","page_range":[1,12]}])
    server.save_chapter_result(wid, "ch1", {
        "chapter_id":"ch1","summary":"","key_points":[],
        "questions":{"multiple_choice":[],"short_answer":[],"reflection":[]}
    })
    r = server.finalize_study(wid, output_format="md_tui")
    _check_envelope(r)
    assert r["ok"] is False
    assert "NotImplemented" in r["error"] or "ROADMAP" in r["error"]
