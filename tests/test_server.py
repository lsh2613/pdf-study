"""server.py 도구 in-process 흐름 테스트.

각 도구의 응답 형식 통일 + 주요 분기 검증.
"""
from __future__ import annotations

import pytest

from pdf_study import server, workspace


def _check_envelope(resp: dict) -> None:
    """모든 도구는 {ok, error, data, next_action} 형태로 응답해야 한다."""
    assert set(resp.keys()) == {"ok", "error", "data", "next_action"}


def _init(pdf, out, **kw):
    r = server.init_work(str(pdf), str(out), **kw)
    return r


def _sc(wid, chapters, **kw):
    """set_chapters — 모드 기본값(sequential/text) 고정 헬퍼."""
    return server.set_chapters(
        wid, chapters, execution_mode="sequential", extraction_mode="text", **kw,
    )


# ---------------------------------------------------------------------------
# init_work — 모드 인자를 더 이상 받지 않는다 (set_chapters에서 결정)
# ---------------------------------------------------------------------------

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
    r = server.init_work(str(ko_short))  # output_dir 생략
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
    weird = tmp_path / "리팩터링 2판-페이지 1.pdf"
    weird.write_bytes(b"%PDF-1.4")  # 진짜 PDF는 아니지만 init_work는 존재만 확인
    monkeypatch.chdir(tmp_path)
    r = server.init_work(str(weird))
    assert r["ok"], r
    out = r["data"]["output_dir"]
    assert out == str(tmp_path / "result" / "리팩터링_2판-페이지_1")


def test_init_work_explicit_output_dir_used_as_is(tmp_path, ko_short):
    target = tmp_path / "my-custom-name"
    r = server.init_work(str(ko_short), str(target))
    assert r["ok"], r
    assert r["data"]["output_dir"] == str(target)
    assert (target / ".work" / "state.json").exists()


# ---------------------------------------------------------------------------
# set_chapters — 처리 모드(순차/병렬 · text/ocr)를 여기서 받는다
# ---------------------------------------------------------------------------

def _assert_mode_choices(r):
    """모드 미지정 거부 응답이 4가지 조합과 특징을 모두 담는지 검증."""
    _check_envelope(r)
    assert r["ok"] is False
    assert "execution_mode" in r["error"] and "extraction_mode" in r["error"]
    combos = {(c["execution_mode"], c["extraction_mode"]) for c in r["data"]["choices"]}
    assert combos == {
        ("sequential", "text"), ("parallel", "text"),
        ("sequential", "ocr"), ("parallel", "ocr"),
    }
    assert all(c.get("label") and c.get("desc") for c in r["data"]["choices"])
    assert r["data"]["execution_modes"] == ["sequential", "parallel"]
    assert r["data"]["extraction_modes"] == ["text", "ocr"]


def test_set_chapters_requires_execution_mode(tmp_path, ko_short):
    """execution_mode 미지정 시 거부 + 4조합 안내."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    r = server.set_chapters(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "page_range": [1, 12]}])  # 모드 생략
    _assert_mode_choices(r)


def test_set_chapters_requires_extraction_mode(tmp_path, ko_short):
    """execution_mode만 주고 extraction_mode 미지정 시 거부 + 4조합 안내."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    r = server.set_chapters(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "page_range": [1, 12]}],
                            execution_mode="sequential")  # extraction_mode 생략
    _assert_mode_choices(r)


# ---------------------------------------------------------------------------
# scan_pdf — 텍스트 레이어 없어도 거부하지 않고 vision 경로
# ---------------------------------------------------------------------------

def test_scan_pdf_scanned_routes_to_vision_not_rejected(tmp_path, scanned_empty):
    wid = server.init_work(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    r = server.scan_pdf(wid)
    _check_envelope(r)
    assert r["ok"] is True, r
    rec = r["data"]["recommendations"]
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert r["data"]["toc_page_images"]
    # 텍스트는 응답에 노출되지 않는다
    assert "scanned_text" not in r["data"]


def test_scan_pdf_outline_routes_to_from_outline(tmp_path, ko_with_toc):
    wid = server.init_work(str(ko_with_toc), str(tmp_path / "out"))["data"]["work_id"]
    r = server.scan_pdf(wid)
    assert r["ok"], r
    rec = r["data"]["recommendations"]
    assert rec["primary_mode"] == "from_outline"
    assert r["data"]["outline_present"] is True
    assert [c["page_range"][0] for c in rec["suggested_chapters"]] == [5, 13, 21]


def test_get_chapter_content_for_unknown_chapter(tmp_path, ko_short):
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
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
    # ko_short은 내장 목차가 없어 vision 경로 — 챕터는 직접 구성
    chs = [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}]

    r3 = _sc(wid, chs, book_info={"title": "T", "author": "A"})
    _check_envelope(r3); assert r3["ok"], r3
    assert r3["data"]["chapter_count"] == 1
    assert "get_subagent_prompts" in r3["next_action"]
    assert "p11-p18" in r3["next_action"]  # 페이지범위 id 금지 경고

    # 본문 가져오기 (text 모드)
    r4 = server.get_chapter_content(wid, "ch1")
    _check_envelope(r4); assert r4["ok"]
    assert r4["data"]["chapter_id"] == "ch1"
    assert r4["data"]["char_count"] > 0
    assert "save_chapter_result" in r4["next_action"]

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
                 "options": ["A", "B"], "answer_index": 0, "explanation": ""}
            ],
            "short_answer": [], "reflection": [],
        },
    })
    _check_envelope(r6); assert r6["ok"]
    assert r6["next_action"] and "list_pending_chapters" in r6["next_action"]

    # extension 결과 저장
    r7 = server.save_extension_result(wid, "ch1", {
        "chapter_id": "ch1", "questions": {"extension": []}
    })
    _check_envelope(r7); assert r7["ok"]
    assert r7["next_action"] and "finalize_study" in r7["next_action"]

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
    assert "finalize_study" in r9["next_action"]

    # finalize
    r10 = server.finalize_study(wid, output_format="html")
    _check_envelope(r10); assert r10["ok"], r10
    out = tmp_path / "out"
    assert (out / "main.html").exists()
    assert not (out / "index.html").exists()

    msg = r10["next_action"]
    assert "study_html.py" in msg
    assert "http://localhost:8765" in msg
    assert "file://" in msg
    assert "Ctrl+C" in msg
    assert "브라우저 탭" in msg
    assert r10["data"]["launch_command"].startswith("cd ")
    assert "study_html.py" in r10["data"]["launch_command"]
    assert r10["data"]["entry_page"] == "main.html"


def test_finalize_requires_output_format(tmp_path, ko_short):
    """output_format 미지정 시 거부 + 사용자에게 물어보라는 안내."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])
    server.save_chapter_result(wid, "ch1", {
        "chapter_id": "ch1", "summary": "", "key_points": [],
        "questions": {"multiple_choice": [], "short_answer": [], "reflection": []}
    })
    r = server.finalize_study(wid)  # output_format 생략
    _check_envelope(r)
    assert r["ok"] is False
    assert "output_format" in r["error"]
    assert r["data"]["choices"] == ["html", "md_tui"]


def test_finalize_rejects_unknown_format(tmp_path, ko_short):
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])
    server.save_chapter_result(wid, "ch1", {
        "chapter_id": "ch1", "summary": "", "key_points": [],
        "questions": {"multiple_choice": [], "short_answer": [], "reflection": []}
    })
    r = server.finalize_study(wid, output_format="bogus")
    _check_envelope(r)
    assert r["ok"] is False
    assert "output_format" in r["error"]


def test_md_tui_renderer_finalizes_ok(tmp_path, ko_short):
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])
    server.save_chapter_result(wid, "ch1", {
        "chapter_id": "ch1", "summary": "본문", "key_points": ["p"],
        "questions": {"multiple_choice": [], "short_answer": [], "reflection": []}
    })
    r = server.finalize_study(wid, output_format="md_tui", force=True)
    _check_envelope(r)
    assert r["ok"] is True, r
    out = tmp_path / "out"
    assert (out / "study_tui.py").exists()
    assert (out / "book.md").exists()
    assert (out / "ch1" / "summary.md").exists()
    assert (out / "ch1" / "quiz.json").exists()
    assert (out / "ch1" / "study_tui.py").exists()
    assert "study_tui.py" in r["next_action"]
    assert "rich" in r["next_action"]
    assert r["data"]["entry_script"] == "study_tui.py"


def test_finalize_blocks_on_pending_then_force(tmp_path, ko_short):
    """pending 챕터가 있으면 finalize는 ok=False로 거부하고 목록을 돌려준다.
    force=True면 부분 결과로 강제 렌더링한다."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"),
                           enable_extension=False)["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 6]},
        {"chapter_id": "ch2", "title": "B", "page_range": [7, 12]},
    ])
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
    wid = server.init_work(str(ko_short), str(out),
                           enable_extension=False)["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [
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
    assert rr["data"]["summary_pending"] == ["ch2"]
    # set_chapters에서 확정된 모드가 보존된다
    assert rr["data"]["execution_mode"] == "sequential"
    assert rr["data"]["extraction_mode"] == "text"

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
