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


def test_init_work_default_output_dir_uses_cwd_result_pdf_basename(tmp_path, ko_short, monkeypatch):
    """output_dir 미지정 시 <cwd>/result/<pdf_basename>/ 로 자동 생성."""
    monkeypatch.chdir(tmp_path)
    r = server.init_work(str(ko_short))   # output_dir 생략
    _check_envelope(r)
    assert r["ok"], r
    out = r["data"]["output_dir"]
    assert out == str(tmp_path / "result" / "ko_short")
    assert (tmp_path / "result" / "ko_short" / ".work" / "state.json").exists()


def test_init_work_blank_output_dir_falls_back_to_default(tmp_path, ko_short, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = server.init_work(str(ko_short), output_dir="   ")  # 공백만
    assert r["ok"], r
    assert r["data"]["output_dir"] == str(tmp_path / "result" / "ko_short")


def test_init_work_default_dir_sanitizes_pdf_name(tmp_path, monkeypatch):
    """공백/특수문자가 있는 PDF 파일명은 _ 로 치환."""
    import shutil
    src = tmp_path.parent  # fixtures가 ko_short 등이 있는 디렉토리
    # 안전한 합성: 빈 PDF로도 sanitize만 확인하면 됨 — init_work는 PDF 존재만 본다
    weird = tmp_path / "리팩터링 2판-페이지 1.pdf"
    weird.write_bytes(b"%PDF-1.4")  # 진짜 PDF는 아니지만 init_work는 존재만 확인
    monkeypatch.chdir(tmp_path)
    r = server.init_work(str(weird))
    assert r["ok"], r
    # 공백 → _, 다른 문자는 한글/숫자/-/. 그대로
    out = r["data"]["output_dir"]
    assert out == str(tmp_path / "result" / "리팩터링_2판-페이지_1")


def test_init_work_explicit_output_dir_used_as_is(tmp_path, ko_short):
    target = tmp_path / "my-custom-name"
    r = server.init_work(str(ko_short), str(target))
    assert r["ok"], r
    assert r["data"]["output_dir"] == str(target)
    assert (target / ".work" / "state.json").exists()


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

    # next_action에 serve.py 명령 + 진도 저장 경고 + 종료 안내가 모두 있어야 한다
    msg = r10["next_action"]
    assert "serve.py" in msg
    assert "http://localhost:8765" in msg
    assert "file://" in msg           # 직접 열기 금지 안내
    assert "Ctrl+C" in msg            # 종료 가이드
    assert "브라우저 탭" in msg        # 탭 닫기로는 안 꺼진다는 안내
    assert r10["data"]["serve_command"].startswith("cd ")
    assert r10["data"]["entry_page"] == "main.html"


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


def test_md_tui_renderer_finalizes_ok(tmp_path, ko_short):
    r1 = server.init_work(str(ko_short), str(tmp_path / "out"), execution_mode="sequential")
    wid = r1["data"]["work_id"]
    server.scan_pdf(wid)
    server.set_chapters(wid, [{"chapter_id":"ch1","title":"전체","page_range":[1,12]}])
    server.save_chapter_result(wid, "ch1", {
        "chapter_id":"ch1","summary":"본문","key_points":["p"],
        "questions":{"multiple_choice":[],"short_answer":[],"reflection":[]}
    })
    r = server.finalize_study(wid, output_format="md_tui", force=True)
    _check_envelope(r)
    assert r["ok"] is True, r
    out = tmp_path / "out"
    assert (out / "study.py").exists()
    assert (out / "book.md").exists()
    assert (out / "ch1" / "summary.md").exists()
    assert (out / "ch1" / "quiz.json").exists()
    assert (out / "ch1" / "study.py").exists()


def test_finalize_blocks_on_pending_then_force(tmp_path, ko_short):
    """pending 챕터가 있으면 finalize는 ok=False로 거부하고 목록을 돌려준다.
    force=True면 부분 결과로 강제 렌더링한다."""
    r1 = server.init_work(str(ko_short), str(tmp_path / "out"),
                          enable_extension=False)
    wid = r1["data"]["work_id"]
    server.scan_pdf(wid)
    server.set_chapters(wid, [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 6]},
        {"chapter_id": "ch2", "title": "B", "page_range": [7, 12]},
    ])
    # ch1만 완료, ch2는 pending
    server.save_chapter_result(wid, "ch1", {
        "chapter_id": "ch1", "summary": "", "key_points": [],
        "questions": {"multiple_choice": [], "short_answer": [], "reflection": []},
    })

    blocked = server.finalize_study(wid, "html")
    _check_envelope(blocked)
    assert blocked["ok"] is False
    assert "ch2" in blocked["error"]
    assert blocked["data"]["summary_pending"] == ["ch2"]
    assert not (tmp_path / "out" / "index.html").exists()

    forced = server.finalize_study(wid, "html", force=True)
    _check_envelope(forced); assert forced["ok"], forced
    assert (tmp_path / "out" / "index.html").exists()


def test_resume_work_restores_registry_after_restart(tmp_path, ko_short):
    """서버 재시작(=레지스트리 소실)을 시뮬레이션한 뒤 resume_work로 복원."""
    out = tmp_path / "out"
    r1 = server.init_work(str(ko_short), str(out), enable_extension=False)
    wid = r1["data"]["work_id"]
    server.scan_pdf(wid)
    server.set_chapters(wid, [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 6]},
        {"chapter_id": "ch2", "title": "B", "page_range": [7, 12]},
    ])
    server.save_chapter_result(wid, "ch1", {
        "chapter_id": "ch1", "summary": "", "key_points": [],
        "questions": {"multiple_choice": [], "short_answer": [], "reflection": []},
    })

    # 서버 재시작 시뮬레이션: in-memory 레지스트리 초기화
    workspace._registry.clear()
    assert not server.get_work_state(wid)["ok"]  # 복원 전엔 unknown work_id

    rr = server.resume_work(output_dir=str(out))
    _check_envelope(rr); assert rr["ok"], rr
    assert rr["data"]["work_id"] == wid
    assert rr["data"]["summary_pending"] == ["ch2"]  # 남은 것만 정확히 보고

    # 복원 후 정상 동작 + 이미 완료된 ch1은 보존
    assert server.get_work_state(wid)["ok"]
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "completed"


def test_resume_work_requires_output_or_pdf():
    r = server.resume_work()
    _check_envelope(r)
    assert r["ok"] is False
    assert "output_dir" in r["error"] or "pdf_path" in r["error"]


def test_resume_work_missing_workspace(tmp_path):
    r = server.resume_work(output_dir=str(tmp_path / "nonexistent"))
    _check_envelope(r)
    assert r["ok"] is False
