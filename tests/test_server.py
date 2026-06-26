"""server.py 도구 in-process 흐름 테스트.

각 도구의 응답 형식 통일 + 주요 분기 검증.
"""
from __future__ import annotations

from unittest.mock import patch
import pytest

from pdf_study import server, workspace


@pytest.fixture(autouse=True)
def stub_scan_toc_ocr(monkeypatch):
    """scan_pdf 목차 OCR 테스트가 실제 PaddleOCR 모델을 로드하지 않게 한다."""
    class StubWorker:
        def process_image(self, img_path):
            return "목차 OCR 텍스트"

    monkeypatch.setattr(server.ocr, "get_ocr_worker", lambda: StubWorker())


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


def _result(summary="요약"):
    """save_chapter_result용 유효 페이로드(summary·key_points·mc/sa/rf 모두 채움)."""
    return {
        "summary": summary, "key_points": ["p1", "p2"],
        "questions": {
            "multiple_choice": [{"id": "mc1", "question": "?", "options": ["A", "B"],
                                 "answer_index": 0, "explanation": ""}],
            "short_answer": [{"id": "sa1", "question": "?", "model_answer": "a"}],
            "reflection": [{"id": "rf1", "question": "?", "model_answer": "a"}],
        },
    }


def _ext():
    """save_extension_result용 유효 페이로드(extension 1개)."""
    return {"questions": {"extension": [
        {"id": "ex1", "question": "?", "context": "c", "model_answer": "a", "sources": []}
    ]}}


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


def test_init_work_docstring_tells_agents_when_to_use_mcp():
    """도구 설명은 PDF 학습 자료 요청을 일반 요약 대신 MCP 워크플로로 유도해야 한다."""
    doc = server.init_work.__doc__ or ""
    assert "PDF 경로" in doc
    assert "학습 자료" in doc
    assert "일반 PDF 요약" in doc
    assert "Do not directly summarize a PDF" in doc
    assert "init_work → scan_pdf → set_chapters" in doc


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
    # 구조화 선택 도구(AskUserQuestion) 사용 + verbatim 정책이 안내에 포함
    assert "AskUserQuestion" in r["error"]


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


def test_mode_choices_narrowed_to_ocr_when_no_text_layer(tmp_path, scanned_empty):
    """스캔본(no_text_layer)이면 모드 미지정 거부 시 OCR 2조합만 제시한다."""
    wid = server.init_work(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    r = server.set_chapters(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "page_range": [1, 3]}])  # 모드 생략
    _check_envelope(r)
    assert r["ok"] is False
    combos = {(c["execution_mode"], c["extraction_mode"]) for c in r["data"]["choices"]}
    assert combos == {("sequential", "ocr"), ("parallel", "ocr")}
    assert r["data"]["extraction_modes"] == ["ocr"]
    assert r["data"]["forced_extraction_mode"] == "ocr"
    assert "AskUserQuestion" in r["error"]


def test_mode_choices_narrowed_to_ocr_when_garbled(tmp_path, ko_short):
    """garbled(mojibake)면 모드 미지정 거부 시 OCR 조합만 제시한다."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    workspace.update_state(wid, text_quality="garbled")
    r = server.set_chapters(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "page_range": [1, 12]}],
                            execution_mode="sequential")  # extraction 생략
    combos = {(c["execution_mode"], c["extraction_mode"]) for c in r["data"]["choices"]}
    assert combos == {("sequential", "ocr"), ("parallel", "ocr")}
    assert r["data"]["extraction_modes"] == ["ocr"]


def test_set_chapters_text_mode_blocked_on_no_text_layer(tmp_path, scanned_empty):
    """스캔본(text_quality=no_text_layer)에 text 모드를 고르면 거부 + OCR 강제."""
    wid = server.init_work(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    assert workspace.load_state(wid)["text_quality"] == "no_text_layer"
    r = server.set_chapters(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "page_range": [1, 3]}],
                            execution_mode="sequential", extraction_mode="text")
    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["forced_extraction_mode"] == "ocr"
    assert r["data"]["execution_mode"] == "sequential"  # 선택한 디스패치는 유지
    assert "ocr" in r["error"].lower()


def test_set_chapters_text_mode_blocked_on_garbled(tmp_path, ko_short):
    """text_quality=garbled면(인코딩 깨짐) text 모드 거부 + OCR 강제."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    workspace.update_state(wid, text_quality="garbled")  # 깨진 텍스트 레이어 가정
    r = server.set_chapters(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "page_range": [1, 12]}],
                            execution_mode="parallel", extraction_mode="text")
    assert r["ok"] is False
    assert r["data"]["text_quality"] == "garbled"
    assert r["data"]["forced_extraction_mode"] == "ocr"


def test_set_chapters_text_mode_ok_when_quality_good(tmp_path, ko_short):
    """정상 텍스트 레이어(medium/high)면 text 모드가 통과한다(오탐 방지)."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    assert workspace.load_state(wid)["text_quality"] in ("low", "medium", "high")
    r = server.set_chapters(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "page_range": [1, 12]}],
                            execution_mode="sequential", extraction_mode="text")
    assert r["ok"] is True, r


def test_get_chapter_content_marks_summary_in_progress(tmp_path, ko_short):
    """get_chapter_content 호출 시 summary_status가 in_progress로, 저장 시 completed로 전이."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"

    server.get_chapter_content(wid, "ch1")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "in_progress"

    server.save_chapter_result(wid, "ch1", _result())
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "completed"

    # 완료 후 다시 본문을 받아가도 completed는 되돌지 않는다
    server.get_chapter_content(wid, "ch1")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "completed"


def test_get_chapter_content_ocr_lazy_extraction(tmp_path, ko_short):
    """OCR 모드에서 get_chapter_content가 페이지를 순차적으로 추출하고 상태에 캐시한다.
    실패한 페이지는 '[해당 페이지 OCR 실패]'를 삽입한다."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    r = server.set_chapters(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 2]}],
        execution_mode="sequential",
        extraction_mode="ocr",
        language="ko",
    )
    assert r["ok"]

    # Mock OCRWorker
    class MockWorker:
        def __init__(self):
            self.call_count = 0
        def process_image(self, img_path):
            self.call_count += 1
            if self.call_count == 1:
                return [[[[[0,0], [0,0], [0,0], [0,0]], ("페이지 1 텍스트", 0.99)]]]
            else:
                raise RuntimeError("OCR Failed")

    mock_worker = MockWorker()
    with patch("pdf_study.pdf.ocr.get_ocr_worker", return_value=mock_worker):

        # First call - should trigger extraction
        content = server.get_chapter_content(wid, "ch1")
        assert content["ok"]
        data = content["data"]
        assert "text" in data
        assert "페이지 1 텍스트" in data["text"]
        assert "[해당 페이지 OCR 실패]" in data["text"]

        # Check that it was cached in state
        state = server.workspace.load_state(wid)
        assert state["chapters"]["ch1"]["body_text"] == data["text"]

        # Second call - should use cache
        mock_worker.call_count = 0
        content2 = server.get_chapter_content(wid, "ch1")
        assert content2["ok"]
        assert content2["data"]["text"] == data["text"]
        assert mock_worker.call_count == 0  # Should not be called again


def test_mark_chapter_in_progress_guards_done_and_missing(tmp_path, ko_short):
    """in_progress 마킹은 completed/skipped를 안 건드리고, 없는 챕터는 조용히 무시."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])

    workspace.mark_chapter_in_progress(wid, "ch1", kind="extension")
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "in_progress"

    workspace.update_chapter_status(wid, "ch1", extension_status="completed")
    workspace.mark_chapter_in_progress(wid, "ch1", kind="extension")  # 되돌지 않음
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "completed"

    workspace.mark_chapter_in_progress(wid, "ch999", kind="summary")  # 없는 챕터 → no-op(예외 없음)


def test_save_chapter_result_rejects_missing_summary(tmp_path, ko_short):
    """summary 누락 시 ok=False로 거부하고 completed로 마킹하지 않는다."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])

    bad = _result()
    del bad["summary"]  # summary 누락(서브에이전트가 성공이라 했지만 빠뜨린 상황)
    r = server.save_chapter_result(wid, "ch1", bad)
    _check_envelope(r)
    assert r["ok"] is False
    assert "summary" in r["data"]["missing"]
    # 거부됐으므로 completed로 넘어가지 않는다
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] != "completed"


def test_save_chapter_result_rejects_empty_enabled_question_type(tmp_path, ko_short):
    """활성화된 문제 유형이 비어 있으면 거부. 비활성 유형은 요구하지 않는다."""
    # reflection만 비활성화 → reflection 비어도 통과, short_answer 비면 거부
    wid = server.init_work(str(ko_short), str(tmp_path / "out"),
                           enable_reflection=False)["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])

    data = _result()
    data["questions"]["reflection"] = []      # 비활성 → 비어도 OK
    data["questions"]["short_answer"] = []     # 활성 → 비면 거부
    r = server.save_chapter_result(wid, "ch1", data)
    assert r["ok"] is False
    assert "questions.short_answer" in r["data"]["missing"]
    assert "questions.reflection" not in r["data"]["missing"]


def test_save_extension_result_rejects_empty_extension(tmp_path, ko_short):
    """questions.extension이 비어 있으면 거부."""
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])
    r = server.save_extension_result(wid, "ch1", {"questions": {"extension": []}})
    assert r["ok"] is False
    assert r["data"]["missing"] == ["questions.extension"]
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] != "completed"


# ---------------------------------------------------------------------------
# scan_pdf — 텍스트 레이어 없어도 거부하지 않고 목차 이미지 OCR 경로
# ---------------------------------------------------------------------------

def test_scan_pdf_scanned_routes_to_toc_ocr_not_rejected(tmp_path, scanned_empty):
    wid = server.init_work(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    r = server.scan_pdf(wid)
    _check_envelope(r)
    assert r["ok"] is True, r
    rec = r["data"]["recommendations"]
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert r["data"]["toc_page_images"]
    assert r["data"]["toc_page_images"][0]["ocr_text"] == "목차 OCR 텍스트"
    assert r["data"]["toc_page_images"][0]["ocr_error"] is None
    assert "ocr_text" in r["next_action"]
    # 텍스트는 응답에 노출되지 않는다
    assert "scanned_text" not in r["data"]


def test_scan_skips_offset_and_language_on_no_text_layer(tmp_path, scanned_empty, monkeypatch):
    """스캔본(no_text_layer)에선 offset/language 측정을 건너뛴다(불필요한 페이지 읽기 회피)."""
    from pdf_study import analysis

    calls = {"offset": 0}
    orig = analysis.reader.detect_page_offset

    def counted(*a, **k):
        calls["offset"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(analysis.reader, "detect_page_offset", counted)
    wid = server.init_work(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    st = workspace.load_state(wid)
    assert st["text_quality"] == "no_text_layer"
    assert st["language"] is None
    assert st["page_offset"] is None
    assert st["page_offset_confidence"] == "none"
    assert calls["offset"] == 0  # detect_page_offset 자체가 호출되지 않음


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
    # ko_short은 내장 목차가 없어 목차 OCR 경로 — 챕터는 직접 구성
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
    r6 = server.save_chapter_result(wid, "ch1", _result())
    _check_envelope(r6); assert r6["ok"]
    assert r6["next_action"] and "list_pending_chapters" in r6["next_action"]

    # extension 결과 저장
    r7 = server.save_extension_result(wid, "ch1", _ext())
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
    server.save_chapter_result(wid, "ch1", _result())
    r = server.finalize_study(wid)  # output_format 생략
    _check_envelope(r)
    assert r["ok"] is False
    assert "output_format" in r["error"]
    assert {c["value"] for c in r["data"]["choices"]} == {"html", "md_tui"}
    # 구조화 선택 도구(AskUserQuestion) 사용 정책이 안내에 포함
    assert "AskUserQuestion" in r["error"]


def test_finalize_rejects_unknown_format(tmp_path, ko_short):
    wid = server.init_work(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    server.scan_pdf(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "page_range": [1, 12]}])
    server.save_chapter_result(wid, "ch1", _result())
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
    server.save_chapter_result(wid, "ch1", _result())

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
    server.save_chapter_result(wid, "ch1", _result())

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
