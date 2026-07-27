"""server.py 도구 in-process 흐름 테스트.

각 도구의 응답 형식 통일 + 주요 분기 검증.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import sys
from types import SimpleNamespace

import pytest

from pdf_study import question_contract, server, workspace
from .conftest import (
    cleanup_test_work,
    ElicitationContext,
    create_test_work as _init,
    finalize_test_study,
    prepare_test_ocr,
    resume_test_work,
    scan_with_question_options as _scan,
    set_test_chapters as _sc,
)


@pytest.fixture(autouse=True)
def stub_scan_toc_ocr(monkeypatch):
    """OCR 테스트가 실제 PaddleOCR 모델을 로드하지 않게 한다."""
    class StubWorker:
        def process_image(self, img_path):
            return "목차 OCR 텍스트"

        def prepare(self):
            return {
                "cache_dir": "fake",
                "models": [],
                "all_cached": True,
                "download_required": False,
                "model_loaded": True,
                "elapsed_sec": 0.0,
            }

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda *args, **kwargs: StubWorker())
    monkeypatch.setattr(server.analysis.ocr, "models_cached", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        server.analysis.ocr,
        "model_cache_status",
        lambda *args, **kwargs: {"cache_dir": "fake", "models": [], "all_cached": True},
    )


def _check_envelope(resp: dict) -> None:
    """모든 도구는 {ok, error, data, next_action} 형태로 응답해야 한다."""
    assert set(resp.keys()) == {"ok", "error", "data", "next_action"}


def _assert_no_choice_fallback(value) -> None:
    if isinstance(value, dict):
        assert "user_choice_required" not in value
        assert "user_choice_instruction" not in value
        for nested in value.values():
            _assert_no_choice_fallback(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_choice_fallback(nested)


def _result(summary="요약"):
    """save_chapter_result용 유효 페이로드(summary·key_points·mc/sa/rf 모두 채움)."""
    result = copy.deepcopy(question_contract.summary_payload_example())
    result["summary"] = summary
    result["key_points"] = ["p1", "p2"]
    questions = result["questions"]
    questions["multiple_choice"][0].update(
        id="mc1", question="?", options=["A", "B"], answer_index=0,
        explanation="A가 정답입니다.",
    )
    questions["short_answer"][0].update(id="sa1", question="?", model_answer="a")
    questions["reflection"][0].update(id="rf1", question="?", model_answer="a")
    return result


def _ext():
    """save_extension_result용 유효 페이로드(extension 1개)."""
    result = copy.deepcopy(question_contract.extension_payload_example())
    result["questions"]["extension"][0].update(
        id="ex1", question="?", model_answer="a",
    )
    return result


def test_save_chapter_result_uses_question_contract(monkeypatch, ko_short, tmp_path):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    monkeypatch.setattr(
        question_contract,
        "missing_summary_fields",
        lambda data, options, chapter_id, **kwargs: ["contract_probe"],
    )

    response = server.save_chapter_result(wid, "ch1", _result())

    assert response["ok"] is False
    assert response["data"]["missing"] == ["contract_probe"]
    assert response["data"]["chapter_id"] == "ch1"


def test_save_chapter_result_materializes_agent_choices_once(tmp_path, ko_short, monkeypatch):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    result = _result()
    item = result["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item.update(correct_answer="정답", incorrect_answers=["오답 A", "오답 B"])
    monkeypatch.setattr(
        question_contract,
        "_shuffle_choices",
        lambda values: values.reverse(),
    )

    assert server.save_chapter_result(wid, "ch1", result)["ok"] is True
    saved = json.loads((workspace.quiz_dir(wid) / "ch1.json").read_text(encoding="utf-8"))
    saved_item = saved["questions"]["multiple_choice"][0]
    assert saved_item["options"] == ["오답 B", "오답 A", "정답"]
    assert saved_item["answer_index"] == 2
    assert "correct_answer" not in saved_item
    assert "incorrect_answers" not in saved_item


def test_save_chapter_result_rejects_invalid_agent_choice_payload_without_files(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    result = _result()
    item = result["questions"]["multiple_choice"][0]
    item.pop("options")
    item.pop("answer_index")
    item["correct_answer"] = "정답"
    item["incorrect_answers"] = ["정답"]

    response = server.save_chapter_result(wid, "ch1", result)

    assert response["ok"] is False
    assert response["data"]["missing"] == ["questions.multiple_choice[0].incorrect_answers"]
    _assert_no_chapter_result_files(wid, "ch1")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"


def test_save_chapter_result_preserves_legacy_choice_payload_order(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    result = _result()
    result["questions"]["multiple_choice"][0].update(
        options=["첫 번째", "두 번째", "세 번째"],
        answer_index=1,
    )

    assert server.save_chapter_result(wid, "ch1", result)["ok"] is True
    saved = json.loads((workspace.quiz_dir(wid) / "ch1.json").read_text(encoding="utf-8"))
    saved_item = saved["questions"]["multiple_choice"][0]
    assert saved_item["options"] == ["첫 번째", "두 번째", "세 번째"]
    assert saved_item["answer_index"] == 1


def test_save_chapter_result_rejects_question_id_saved_by_extension(
    tmp_path, ko_short,
):
    """동시에 처리 가능한 두 결과도 챕터 안에서는 같은 문제 ID를 쓸 수 없다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    extension = _ext()
    extension["questions"]["extension"][0]["id"] = "shared"
    assert server.save_extension_result(wid, "ch1", extension)["ok"] is True

    summary = _result()
    summary["questions"]["multiple_choice"][0]["id"] = "shared"
    response = server.save_chapter_result(wid, "ch1", summary)

    assert response["ok"] is False
    assert response["data"]["missing"] == ["questions.multiple_choice[0].id"]
    assert not (workspace.summaries_dir(wid) / "ch1.json").exists()
    assert not (workspace.quiz_dir(wid) / "ch1.json").exists()
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"


def test_save_chapter_result_enforces_question_maximum_from_chapter_text(
    tmp_path, ko_short,
):
    wid = _init(str(ko_short), str(tmp_path / "out_maximum"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    workspace.update_chapter_status(wid, "ch1", char_count=2_999)

    summary = _result()
    multiple_choice = summary["questions"]["multiple_choice"][0]
    summary["questions"]["multiple_choice"] = [
        {**copy.deepcopy(multiple_choice), "id": f"mc{index}"}
        for index in range(4)
    ]
    response = server.save_chapter_result(wid, "ch1", summary)

    assert response["ok"] is False
    assert response["data"]["missing"] == ["questions.multiple_choice"]
    _assert_no_chapter_result_files(wid, "ch1")


def _assert_no_chapter_result_files(wid: str, chapter_id: str) -> None:
    assert not (workspace.summaries_dir(wid) / f"{chapter_id}.json").exists()
    assert not (workspace.quiz_dir(wid) / f"{chapter_id}.json").exists()
    assert not (workspace.extension_quiz_dir(wid) / f"{chapter_id}.json").exists()


def test_set_chapters_preserves_page_metadata_end_to_end(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out_pages"))["data"]["work_id"]
    _scan(wid)

    result = _sc(wid, [
        {
            "chapter_id": "ch1",
            "title": "서문",
            "pdf_pages": [1, 6],
            "source_pages": None,
        },
        {
            "chapter_id": "ch2",
            "title": "본문",
            "pdf_pages": [7, 12],
            "source_pages": [1, 6],
        },
    ])

    assert result["ok"], result
    state_chapters = workspace.load_state(wid)["chapters"]
    for index, (chapter_id, pdf_pages, source_pages) in enumerate((
        ("ch1", [1, 6], None),
        ("ch2", [7, 12], [1, 6]),
    )):
        for chapter in (
            result["data"]["chapters"][index],
            state_chapters[chapter_id],
            workspace.get_chapter_raw(wid, chapter_id),
        ):
            assert chapter["pdf_pages"] == pdf_pages
            assert chapter["source_pages"] == source_pages
            assert "page_range" not in chapter
            assert "printed_range" not in chapter


# ---------------------------------------------------------------------------
# init_work — 모드 인자를 더 이상 받지 않는다 (set_chapters에서 결정)
# ---------------------------------------------------------------------------

def test_init_work_elicits_optional_question_types_and_user_context(tmp_path, ko_short):
    ctx = ElicitationContext(
        cwd=tmp_path,
        responses=[{
            "enable_short_answer": True,
            "enable_reflection": False,
            "enable_extension": True,
            "user_context": "데이터베이스를 처음 배우는 직장인",
        }],
    )
    r = asyncio.run(server.init_work(pdf_path=str(ko_short), ctx=ctx))

    _check_envelope(r)
    assert r["ok"] is True
    assert r["data"]["question_options"] == {
        "multiple_choice": True,
        "short_answer": True,
        "reflection": False,
        "extension": True,
    }
    assert "question_setup" not in r["data"]
    assert "단답형 문제를 생성할까요?" in ctx.messages[0]
    assert (
        workspace.load_state(r["data"]["work_id"])["user_context"]
        == "데이터베이스를 처음 배우는 직장인"
    )
    assert "scan_pdf" in r["next_action"]
    _assert_no_choice_fallback(r)


def test_scan_pdf_requires_elicitation_without_scanning(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]

    r = asyncio.run(server.scan_pdf(
        work_id=wid,
        ctx=ElicitationContext(supported=False),
    ))

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["required_capability"] == "elicitation.form"
    state = workspace.load_state(wid)
    assert state["current_phase"] == "init"
    assert state["page_count"] is None
    assert state["question_options"]["extension"] is None


def test_scan_pdf_confirms_choices_and_user_context(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]

    r = asyncio.run(server.scan_pdf(
        work_id=wid,
        ctx=ElicitationContext(responses=[{
            "enable_short_answer": True,
            "enable_reflection": False,
            "enable_extension": True,
            "user_context": "  데이터베이스를 처음 배우는 직장인  ",
        }]),
    ))

    assert r["ok"], r
    assert r["data"]["question_options"] == {
        "multiple_choice": True,
        "short_answer": True,
        "reflection": False,
        "extension": True,
    }
    assert r["data"]["user_context"] == "데이터베이스를 처음 배우는 직장인"
    state = workspace.load_state(wid)
    assert state["question_options"] == r["data"]["question_options"]
    assert state["user_context"] == r["data"]["user_context"]


def test_scan_pdf_invalid_elicitation_data_never_exposes_choice_fallback(
    tmp_path, ko_short,
):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    ctx = ElicitationContext()

    async def incomplete_elicitation(message, schema):
        return SimpleNamespace(
            action="accept",
            data=SimpleNamespace(model_dump=lambda: {
                "enable_short_answer": True,
            }),
        )

    ctx.elicit = incomplete_elicitation
    response = asyncio.run(server.scan_pdf(work_id=wid, ctx=ctx))

    assert response["ok"] is False
    assert response["data"]["missing"] == [
        "enable_reflection",
        "enable_extension",
    ]
    _assert_no_choice_fallback(response)
    assert workspace.load_state(wid)["page_count"] is None


def test_scan_pdf_does_not_silently_change_confirmed_choice(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    first = asyncio.run(server.scan_pdf(
        work_id=wid,
        ctx=ElicitationContext(responses=[{
            "enable_short_answer": True,
            "enable_reflection": False,
            "enable_extension": False,
        }]),
    ))
    assert first["ok"], first

    changed = asyncio.run(server.scan_pdf(
        work_id=wid,
        ctx=ElicitationContext(),
    ))

    assert changed["ok"] is True
    assert workspace.load_state(wid)["question_options"]["extension"] is False


def test_init_work_rejects_missing_pdf(tmp_path):
    r = _init(str(tmp_path / "nope.pdf"), str(tmp_path / "out"))
    _check_envelope(r)
    assert r["ok"] is False


def test_init_work_default_output_dir_uses_agent_cwd_result_pdf_basename(
    tmp_path, ko_short,
):
    """output_dir 미지정 시 명시된 agent cwd 아래 기본 폴더를 만든다."""
    r = asyncio.run(server.init_work(
        pdf_path=str(ko_short),
        ctx=ElicitationContext(cwd=tmp_path, responses=[{
            "enable_short_answer": True,
            "enable_reflection": True,
            "enable_extension": True,
            "user_context": "",
        }]),
    ))
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
    assert "scan_pdf → prepare_ocr → scan_toc_with_ocr → set_chapters" in doc
    assert "scan_pdf는 OCR 모델 다운로드/로드/실행을 하지 않고" in doc


def test_init_work_docstring_rejects_server_startup_directory_fallback():
    doc = server.init_work.__doc__ or ""

    assert "현재 agent workspace" in doc
    assert "서버 실행 cwd로 폴백하지 않고" in doc


def test_init_work_docstring_describes_fixed_workspace_output_path():
    doc = server.init_work.__doc__ or ""

    assert "현재 agent workspace" in doc
    assert "result/<pdf_basename>" in doc


def test_init_work_default_dir_sanitizes_pdf_name(tmp_path):
    """공백/특수문자가 있는 PDF 파일명은 _ 로 치환."""
    weird = tmp_path / "리팩터링 2판-페이지 1.pdf"
    weird.write_bytes(b"%PDF-1.4")  # 진짜 PDF는 아니지만 init_work는 존재만 확인
    r = asyncio.run(server.init_work(
        pdf_path=str(weird),
        ctx=ElicitationContext(cwd=tmp_path, responses=[{
            "enable_short_answer": True,
            "enable_reflection": True,
            "enable_extension": True,
            "user_context": "",
        }]),
    ))
    assert r["ok"], r
    out = r["data"]["output_dir"]
    assert out == str(tmp_path / "result" / "리팩터링_2판-페이지_1")


def test_init_work_existing_output_returns_metadata_without_fallback(tmp_path, ko_short):
    out = tmp_path / "out"
    first = _init(str(ko_short), str(out))
    assert first["ok"], first
    state_path = out / ".work" / "state.json"
    before = state_path.read_bytes()

    repeated = _init(str(ko_short), str(out))

    _check_envelope(repeated)
    assert repeated["ok"] is False
    assert repeated["data"]["existing_work"]["work_id"] == first["data"]["work_id"]
    assert "choices" not in repeated["data"]
    assert repeated["next_action"] is None
    _assert_no_choice_fallback(repeated)
    assert state_path.read_bytes() == before


def test_init_work_replace_existing_clears_old_work_only(
    tmp_path, ko_short, monkeypatch
):
    ids = iter(["old-work", "new-work"])
    monkeypatch.setattr(server.workspace, "make_work_id", lambda: next(ids))
    out = tmp_path / "out"
    first = _init(str(ko_short), str(out))
    assert first["ok"], first
    stale = out / ".work" / "chapters" / "summaries" / "ch1.json"
    stale.write_text('{"summary": "old"}', encoding="utf-8")
    rendered = out / "ch1.html"
    rendered.write_text("old rendered study", encoding="utf-8")
    unrelated = out / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    replaced = _init(
        str(ko_short),
        str(out),
        replace_existing=True,
    )

    assert replaced["ok"], replaced
    assert replaced["data"]["work_id"] == "new-work"
    assert not stale.exists()
    assert rendered.read_text(encoding="utf-8") == "old rendered study"
    assert unrelated.read_text(encoding="utf-8") == "keep me"
    assert workspace.load_state("new-work")["current_phase"] == "init"


def test_init_work_replace_existing_validates_before_removing_old_work(
    tmp_path, ko_short
):
    out = tmp_path / "out"
    first = _init(str(ko_short), str(out))
    assert first["ok"], first
    state_path = out / ".work" / "state.json"
    before = state_path.read_bytes()

    rejected = _init(
        str(tmp_path / "missing.pdf"),
        str(out),
        replace_existing=True,
    )

    assert rejected["ok"] is False
    assert "PDF not found" in rejected["error"]
    assert state_path.read_bytes() == before


def test_init_work_rendered_output_without_state_has_no_choice_fallback(
    tmp_path, ko_short
):
    out = tmp_path / "legacy"
    (out / "assets").mkdir(parents=True)
    (out / "study_html.py").write_text("# launcher", encoding="utf-8")
    (out / "index.html").write_text("<html></html>", encoding="utf-8")

    collision = _init(str(ko_short), str(out))

    assert collision["ok"] is False
    assert "choices" not in collision["data"]
    _assert_no_choice_fallback(collision)


# ---------------------------------------------------------------------------
# set_chapters — 처리 모드(순차/병렬 · text/ocr)를 Elicitation으로 받는다
# ---------------------------------------------------------------------------


def test_scan_pdf_exposes_choice_free_set_chapters_step(tmp_path, ko_with_toc):
    """정상 scan_pdf 응답은 에이전트가 생성할 chapters만 다음 입력으로 요구한다."""
    wid = _init(str(ko_with_toc), str(tmp_path / "out"))["data"]["work_id"]

    scanned = _scan(wid)

    assert scanned["ok"], scanned
    recommendations = scanned["data"]["recommendations"]
    assert "user_choice_options" not in recommendations
    assert "user_choices" not in recommendations

    next_step = scanned["data"]["next_step"]
    assert next_step == {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }
    _assert_no_choice_fallback(scanned)


def test_scan_pdf_keeps_choice_free_step_when_text_is_unavailable(
    tmp_path, scanned_empty,
):
    """text가 금지된 PDF도 실패 호출 없이 유효한 다음 모드만 제공한다."""
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]

    scanned = _scan(wid)

    assert scanned["ok"], scanned
    set_chapters_step = scanned["data"]["set_chapters_next_step"]
    assert set_chapters_step == {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }


def test_scan_pdf_routes_forced_ocr_to_eliciting_prepare_tool(tmp_path, scanned_empty):
    """텍스트 레이어가 없으면 선택 파라미터 없이 prepare_ocr로 이동한다."""
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]

    scanned = _scan(wid)

    assert scanned["ok"] is True
    assert "ocr_language_setup" not in scanned["data"]
    assert scanned["data"]["next_step"]["tool"] == "prepare_ocr"
    assert scanned["data"]["next_step"]["required_parameters"] == []


def test_prepare_ocr_persists_selected_language(tmp_path, scanned_empty, monkeypatch):
    """선택한 OCR 언어는 모델 준비가 성공한 뒤 작업 상태에 보존된다."""
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    captured: list[str] = []

    class Worker:
        def prepare(self):
            return {"models": [], "all_cached": True}

    def fake_worker(ocr_language="korean"):
        captured.append(ocr_language)
        return Worker()

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", fake_worker)

    prepared = prepare_test_ocr(wid, "english")

    assert prepared["ok"] is True, prepared
    assert captured == ["english"]
    assert workspace.load_state(wid)["ocr_language"] == "english"


def test_set_chapters_elicitation_rejects_text_on_no_text_layer(tmp_path, scanned_empty):
    """스캔본에서는 Elicitation에 없는 text 값을 받아들이지 않는다."""
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    assert workspace.load_state(wid)["text_quality"] == "no_text_layer"
    r = _sc(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "pdf_pages": [1, 3]}],
                            execution_mode="sequential", extraction_mode="text")
    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"] is None
    assert "지원하지 않는 본문 추출 방식" in r["error"]
    assert workspace.load_state(wid)["chapters"] == {}


def test_set_chapters_elicitation_rejects_text_on_garbled(tmp_path, ko_short):
    """garbled PDF에서도 Elicitation에 없는 text 값을 받아들이지 않는다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    workspace.update_state(wid, text_quality="garbled")  # 깨진 텍스트 레이어 가정
    r = _sc(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "pdf_pages": [1, 12]}],
                            execution_mode="parallel", extraction_mode="text")
    assert r["ok"] is False
    assert "지원하지 않는 본문 추출 방식" in r["error"]
    assert workspace.load_state(wid)["chapters"] == {}


def test_set_chapters_text_mode_ok_when_quality_good(tmp_path, ko_short):
    """정상 텍스트 레이어(medium/high)면 text 모드가 통과한다(오탐 방지)."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    assert workspace.load_state(wid)["text_quality"] in ("low", "medium", "high")
    r = _sc(wid, [{"chapter_id": "ch1", "title": "전체",
                                   "pdf_pages": [1, 12]}],
                            execution_mode="sequential", extraction_mode="text")
    assert r["ok"] is True, r


def test_get_chapter_content_marks_summary_in_progress(tmp_path, ko_short):
    """get_chapter_content 호출 시 summary_status가 in_progress로, 저장 시 completed로 전이."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"

    server.get_chapter_content(wid, "ch1")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "in_progress"

    server.save_chapter_result(wid, "ch1", _result())
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "completed"

    # 완료 후 다시 본문을 받아가도 completed는 되돌지 않는다
    server.get_chapter_content(wid, "ch1")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "completed"


def test_get_chapter_content_ocr_returns_precomputed_text_without_lazy_ocr(
    tmp_path, ko_short, monkeypatch
):
    """OCR 모드에서도 get_chapter_content는 저장된 text만 반환하고 worker를 다시 부르지 않는다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    calls = []

    class MockWorker:
        def process_image(self, img_path):
            calls.append(img_path)
            return "페이지 OCR 텍스트"

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    r = _sc(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 2]}],
        execution_mode="sequential",
        extraction_mode="ocr",
    )
    assert r["ok"]
    assert len(calls) == 2
    assert workspace.get_chapter_raw(wid, "ch1")["text"] == (
        "페이지 OCR 텍스트\n\n페이지 OCR 텍스트"
    )
    raw_with_legacy_images = workspace.get_chapter_raw(wid, "ch1")
    raw_with_legacy_images["page_images"] = [{"path": "legacy.jpg"}]
    workspace.save_chapter_raw(wid, "ch1", raw_with_legacy_images)

    class FailingWorker:
        def process_image(self, img_path):
            raise AssertionError("lazy OCR must not run")

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda: FailingWorker())
    content = server.get_chapter_content(wid, "ch1")
    assert content["ok"]
    data = content["data"]
    assert data["text"] == "페이지 OCR 텍스트\n\n페이지 OCR 텍스트"
    assert "page_images" not in data
    assert "body_text" not in workspace.load_state(wid)["chapters"]["ch1"]

    content2 = server.get_chapter_content(wid, "ch1")
    assert content2["ok"]
    assert content2["data"]["text"] == data["text"]


def test_set_chapters_ocr_failure_returns_failed_chapters(
    tmp_path, ko_short, monkeypatch
):
    """OCR 선처리 실패는 ok=false와 data.failed_chapters로 즉시 드러난다."""
    wid = _init(str(ko_short), str(tmp_path / "out_ocr_fail"))["data"]["work_id"]
    _scan(wid)

    class MockWorker:
        def process_image(self, img_path):
            if str(img_path).endswith("p2.jpg"):
                raise RuntimeError("OCR boom")
            return "partial"

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    r = _sc(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 2]}],
        execution_mode="parallel",
        extraction_mode="ocr",
    )

    _check_envelope(r)
    assert r["ok"] is False
    assert r["next_action"] is None
    failed = r["data"]["failed_chapters"]
    assert failed[0]["chapter_id"] == "ch1"
    assert failed[0]["failed_pages"] == [2]
    assert "OCR boom" in failed[0]["error"]
    state = workspace.load_state(wid)
    assert state["chapters"]["ch1"]["summary_status"] == "failed"
    assert state["phases"]["chapter_setup"] == "completed"
    assert state["phases"]["chapter_processing"] == "failed"


def test_set_chapters_ocr_requires_prepare_when_cache_missing(
    tmp_path, ko_short, monkeypatch
):
    wid = _init(str(ko_short), str(tmp_path / "out_ocr_missing"))["data"]["work_id"]
    _scan(wid)
    monkeypatch.setattr(server.analysis.ocr, "models_cached", lambda: False)
    monkeypatch.setattr(
        server.analysis.ocr,
        "model_cache_status",
        lambda: {"cache_dir": "fake", "models": [], "all_cached": False},
    )

    r = _sc(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 1]}],
        execution_mode="sequential",
        extraction_mode="ocr",
    )

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["forced_next_step"] == "prepare_ocr"
    assert r["next_action"] == f'prepare_ocr(work_id="{wid}")'


def test_save_chapter_result_accepts_without_body_text(tmp_path, ko_short):
    """body_text 없이도 요약/문제 저장은 정상 완료된다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    r = server.save_chapter_result(wid, "ch1", _result())

    assert r["ok"], r
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "completed"


def test_save_chapter_result_body_text_does_not_overwrite_ocr_raw(
    tmp_path, ko_short, monkeypatch
):
    """server 경계로 body_text가 들어와도 OCR raw text/char_count는 유지된다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)

    class MockWorker:
        def process_image(self, img_path):
            return "선계산 OCR 본문"

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    r = _sc(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 1]}],
        execution_mode="sequential",
        extraction_mode="ocr",
    )
    assert r["ok"], r
    raw0 = workspace.get_chapter_raw(wid, "ch1")

    data = _result()
    data["body_text"] = "서브에이전트가 보낸 덮어쓰기 후보"
    r = server.save_chapter_result(wid, "ch1", data)

    assert r["ok"], r
    raw1 = workspace.get_chapter_raw(wid, "ch1")
    assert raw1 == raw0
    assert workspace.load_state(wid)["chapters"]["ch1"]["char_count"] == raw0["char_count"]


def test_save_chapter_result_rejects_bad_work_id_without_files(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    r = server.save_chapter_result(f"{wid}-missing", "ch1", _result())

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["missing"] == ["work_id"]
    _assert_no_chapter_result_files(wid, "ch1")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"


def test_save_chapter_result_rejects_unknown_chapter_without_files(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    r = server.save_chapter_result(wid, "ch999", _result())

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["missing"] == ["chapter_id"]
    _assert_no_chapter_result_files(wid, "ch999")
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"


def test_save_chapter_result_reports_target_before_payload_shape(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    r = server.save_chapter_result(wid, "ch999", {"questions": {}})

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["missing"] == ["chapter_id"]
    _assert_no_chapter_result_files(wid, "ch999")


def test_save_results_reject_skip_chapter_without_files(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "색인", "pdf_pages": [1, 1], "skip": True},
    ])

    summary = server.save_chapter_result(wid, "ch1", _result())
    extension = server.save_extension_result(wid, "ch1", _ext())

    _check_envelope(summary)
    _check_envelope(extension)
    assert summary["ok"] is False
    assert extension["ok"] is False
    assert summary["data"]["missing"] == ["chapter_id"]
    assert extension["data"]["missing"] == ["chapter_id"]
    _assert_no_chapter_result_files(wid, "ch1")
    entry = workspace.load_state(wid)["chapters"]["ch1"]
    assert entry["summary_status"] == "skipped"
    assert entry["extension_status"] == "skipped"


def test_save_extension_result_rejects_bad_targets_without_files(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    bad_work = server.save_extension_result(f"{wid}-missing", "ch1", _ext())
    unknown_chapter = server.save_extension_result(wid, "ch999", _ext())

    _check_envelope(bad_work)
    _check_envelope(unknown_chapter)
    assert bad_work["ok"] is False
    assert unknown_chapter["ok"] is False
    assert bad_work["data"]["missing"] == ["work_id"]
    assert unknown_chapter["data"]["missing"] == ["chapter_id"]
    _assert_no_chapter_result_files(wid, "ch1")
    _assert_no_chapter_result_files(wid, "ch999")
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "pending"


def test_save_extension_result_reports_target_before_payload_shape(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    bad_work = server.save_extension_result(f"{wid}-missing", "ch1", {})
    unknown_chapter = server.save_extension_result(wid, "ch999", {})

    _check_envelope(bad_work)
    _check_envelope(unknown_chapter)
    assert bad_work["ok"] is False
    assert unknown_chapter["ok"] is False
    assert bad_work["data"]["missing"] == ["work_id"]
    assert unknown_chapter["data"]["missing"] == ["chapter_id"]
    _assert_no_chapter_result_files(wid, "ch1")
    _assert_no_chapter_result_files(wid, "ch999")


def test_save_chapter_result_reports_state_failure_without_files(
    tmp_path, ko_short, monkeypatch
):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    original_save_state = workspace.save_state

    def fail_completed_state(work_id, state):
        if state["chapters"]["ch1"].get("summary_status") == "completed":
            raise RuntimeError("state write failed")
        original_save_state(work_id, state)

    monkeypatch.setattr(workspace, "save_state", fail_completed_state)

    r = server.save_chapter_result(wid, "ch1", _result())

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["missing"] == ["state"]
    _assert_no_chapter_result_files(wid, "ch1")


def test_save_extension_result_reports_state_failure_without_files(
    tmp_path, ko_short, monkeypatch
):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    original_save_state = workspace.save_state

    def fail_completed_state(work_id, state):
        if state["chapters"]["ch1"].get("extension_status") == "completed":
            raise RuntimeError("state write failed")
        original_save_state(work_id, state)

    monkeypatch.setattr(workspace, "save_state", fail_completed_state)

    r = server.save_extension_result(wid, "ch1", _ext())

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["missing"] == ["state"]
    _assert_no_chapter_result_files(wid, "ch1")


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("raw_missing", "raw_missing"),
        ("text_missing", "text_missing"),
        ("char_count_mismatch", "char_count_mismatch"),
        ("failed_status", "ocr_failed"),
    ],
)
def test_get_subagent_prompts_rejects_invalid_ocr_raw(
    tmp_path, ko_short, monkeypatch, case, expected_code
):
    """OCR 모드에서 raw text/char_count가 불완전하면 sub-agent 프롬프트를 주지 않는다."""
    wid = _init(str(ko_short), str(tmp_path / f"out_{case}"))["data"]["work_id"]
    _scan(wid)

    class MockWorker:
        def process_image(self, img_path):
            return "정상 OCR 본문"

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    r = _sc(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 1]}],
        execution_mode="sequential",
        extraction_mode="ocr",
    )
    assert r["ok"], r

    if case == "raw_missing":
        (workspace.chapters_raw_dir(wid) / "ch1.json").unlink()
    elif case == "text_missing":
        workspace.save_chapter_raw(wid, "ch1", {
            "chapter_id": "ch1",
            "title": "전체",
            "pdf_pages": [1, 1],
            "char_count": 1,
        })
    elif case == "char_count_mismatch":
        workspace.save_chapter_raw(wid, "ch1", {
            "chapter_id": "ch1",
            "title": "전체",
            "pdf_pages": [1, 1],
            "text": "정상 OCR 본문",
            "char_count": 999,
        })
    elif case == "failed_status":
        workspace.update_chapter_status(
            wid, "ch1", summary_status="failed", error="OCR failed", failed_pages=[1]
        )

    r = server.get_subagent_prompts(wid)

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["required_fields"] == ["chapter_raw.text", "chapter_raw.char_count"]
    invalid = r["data"]["invalid_chapters"]
    assert invalid[0]["chapter_id"] == "ch1"
    codes = {reason["code"] for reason in invalid[0]["reasons"]}
    assert expected_code in codes
    if expected_code == "ocr_failed":
        failed = r["data"]["failed_chapters"]
        assert failed == [{
            "chapter_id": "ch1",
            "failed_pages": [1],
            "error": "OCR failed",
        }]
    else:
        assert r["data"]["failed_chapters"] == []


def test_get_subagent_prompts_validates_only_pending_raw(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out_pending_raw"))["data"]["work_id"]
    _scan(wid)
    result = _sc(wid, [
        {"chapter_id": "ch1", "title": "완료", "pdf_pages": [1, 6]},
        {"chapter_id": "ch2", "title": "대기", "pdf_pages": [7, 12]},
    ])
    assert result["ok"], result
    assert server.save_chapter_result(wid, "ch1", _result())["ok"] is True
    assert server.save_extension_result(wid, "ch1", _ext())["ok"] is True

    (workspace.chapters_raw_dir(wid) / "ch1.json").unlink()

    response = server.get_subagent_prompts(wid)
    assert response["ok"] is True
    assert response["data"]["chapter_ids"] == ["ch2"]
    assert response["data"]["summary_pending_chapter_ids"] == ["ch2"]
    assert response["data"]["extension_pending_chapter_ids"] == ["ch2"]

    (workspace.chapters_raw_dir(wid) / "ch2.json").unlink()

    response = server.get_subagent_prompts(wid)
    assert response["ok"] is False
    assert [item["chapter_id"] for item in response["data"]["invalid_chapters"]] == ["ch2"]
    assert "pending 챕터" in response["error"]
    assert "각 non-skip 챕터" not in response["error"]


def test_get_subagent_prompts_ignores_missing_raw_when_all_results_completed(
    tmp_path, ko_short,
):
    wid = _init(
        str(ko_short), str(tmp_path / "out_all_completed"), enable_extension=True,
    )["data"]["work_id"]
    _scan(wid)
    result = _sc(
        wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}],
    )
    assert result["ok"], result
    assert server.save_chapter_result(wid, "ch1", _result())["ok"] is True
    assert server.save_extension_result(wid, "ch1", _ext())["ok"] is True

    (workspace.chapters_raw_dir(wid) / "ch1.json").unlink()

    response = server.get_subagent_prompts(wid)

    assert response["ok"] is True, response
    assert response["data"]["summary_pending_chapter_ids"] == []
    assert response["data"]["extension_pending_chapter_ids"] == []
    assert response["data"]["chapter_ids"] == []
    assert "list_pending_chapters" in response["next_action"]
    assert response["next_action"].index("list_pending_chapters") < response["next_action"].index("finalize_study")
    assert "save_chapter_result" not in response["next_action"]
    assert "save_extension_result" not in response["next_action"]


def test_get_subagent_prompts_rejects_work_without_chapter_setup(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)

    response = server.get_subagent_prompts(wid)

    assert response["ok"] is False
    assert response["data"]["chapter_setup"] != "completed"
    assert "set_chapters" in response["next_action"]
    assert "finalize_study" not in response["next_action"]


def test_get_subagent_prompts_next_action_mentions_only_summary_when_pending(
    tmp_path, ko_short,
):
    wid = _init(str(ko_short), str(tmp_path / "out_summary"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    assert server.save_extension_result(wid, "ch1", _ext())["ok"] is True

    response = server.get_subagent_prompts(wid)

    assert response["ok"] is True, response
    assert response["data"]["summary_pending_chapter_ids"] == ["ch1"]
    assert response["data"]["extension_pending_chapter_ids"] == []
    assert "save_chapter_result" in response["next_action"]
    assert "save_extension_result" not in response["next_action"]


def test_get_subagent_prompts_next_action_mentions_only_extension_when_pending(
    tmp_path, ko_short,
):
    wid = _init(str(ko_short), str(tmp_path / "out_extension"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    assert server.save_chapter_result(wid, "ch1", _result())["ok"] is True

    response = server.get_subagent_prompts(wid)

    assert response["ok"] is True, response
    assert response["data"]["summary_pending_chapter_ids"] == []
    assert response["data"]["extension_pending_chapter_ids"] == ["ch1"]
    assert "save_chapter_result" not in response["next_action"]
    assert "save_extension_result" in response["next_action"]


def test_get_subagent_prompts_next_action_omits_extension_when_disabled(
    tmp_path, ko_short,
):
    wid = _init(
        str(ko_short), str(tmp_path / "out_disabled"), enable_extension=False,
    )["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    response = server.get_subagent_prompts(wid)

    assert response["ok"] is True, response
    assert response["data"]["summary_pending_chapter_ids"] == ["ch1"]
    assert response["data"]["extension_pending_chapter_ids"] == []
    assert "save_chapter_result" in response["next_action"]
    assert "save_extension_result" not in response["next_action"]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("raw_missing", "raw_missing"),
        ("char_count_mismatch", "char_count_mismatch"),
    ],
)
def test_get_chapter_content_rejects_invalid_raw(
    tmp_path, ko_short, monkeypatch, case, expected
):
    """get_chapter_content도 raw 누락/char_count 불일치를 실패로 반환한다."""
    wid = _init(str(ko_short), str(tmp_path / f"out_content_{case}"))["data"]["work_id"]
    _scan(wid)

    class MockWorker:
        def process_image(self, img_path):
            return "정상 OCR 본문"

    monkeypatch.setattr(server.analysis.ocr, "get_ocr_worker", lambda: MockWorker())
    r = _sc(
        wid,
        [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 1]}],
        execution_mode="sequential",
        extraction_mode="ocr",
    )
    assert r["ok"], r

    if case == "raw_missing":
        (workspace.chapters_raw_dir(wid) / "ch1.json").unlink()
    else:
        workspace.save_chapter_raw(wid, "ch1", {
            "chapter_id": "ch1",
            "title": "전체",
            "pdf_pages": [1, 1],
            "text": "정상 OCR 본문",
            "char_count": 999,
            "page_images": [{"path": "must-not-return.jpg"}],
        })

    r = server.get_chapter_content(wid, "ch1")

    _check_envelope(r)
    assert r["ok"] is False
    assert expected in r["error"]


def test_mark_chapter_in_progress_guards_done_and_missing(tmp_path, ko_short):
    """in_progress 마킹은 completed/skipped를 안 건드리고, 없는 챕터는 조용히 무시."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    workspace.mark_chapter_in_progress(wid, "ch1", kind="extension")
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "in_progress"

    workspace.update_chapter_status(wid, "ch1", extension_status="completed")
    workspace.mark_chapter_in_progress(wid, "ch1", kind="extension")  # 되돌지 않음
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "completed"

    workspace.mark_chapter_in_progress(wid, "ch999", kind="summary")  # 없는 챕터 → no-op(예외 없음)


def test_save_chapter_result_rejects_missing_summary(tmp_path, ko_short):
    """summary 누락 시 ok=False로 거부하고 completed로 마킹하지 않는다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

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
    wid = _init(str(ko_short), str(tmp_path / "out"),
                           enable_reflection=False)["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    data = _result()
    data["questions"]["reflection"] = []      # 비활성 → 비어도 OK
    data["questions"]["short_answer"] = []     # 활성 → 비면 거부
    r = server.save_chapter_result(wid, "ch1", data)
    assert r["ok"] is False
    assert "questions.short_answer" in r["data"]["missing"]
    assert "questions.reflection" not in r["data"]["missing"]


@pytest.mark.parametrize(
    ("mutate", "expected_missing"),
    [
        (lambda d: d.update({"chapter_id": "ch2"}), "chapter_id"),
        (lambda d: d.update({"title": 123}), "title"),
        (lambda d: d.update({"summary": "   "}), "summary"),
        (lambda d: d.update({"key_points": ["p1", ""]}), "key_points[1]"),
        (lambda d: d["questions"].pop("reflection"), "questions.reflection"),
        (
            lambda d: d["questions"].__setitem__("multiple_choice", {}),
            "questions.multiple_choice",
        ),
        (
            lambda d: d["questions"]["multiple_choice"][0].pop("explanation"),
            "questions.multiple_choice[0].explanation",
        ),
        (
            lambda d: d["questions"]["multiple_choice"][0].__setitem__(
                "options", ["A"]
            ),
            "questions.multiple_choice[0].options",
        ),
        (
            lambda d: d["questions"]["multiple_choice"][0].__setitem__(
                "answer_index", 2
            ),
            "questions.multiple_choice[0].answer_index",
        ),
        (
            lambda d: d["questions"]["short_answer"][0].__setitem__(
                "model_answer", ""
            ),
            "questions.short_answer[0].model_answer",
        ),
        (
            lambda d: d["questions"]["reflection"][0].__setitem__(
                "question", "   "
            ),
            "questions.reflection[0].question",
        ),
    ],
)
def test_save_chapter_result_rejects_invalid_json_shape(
    tmp_path, ko_short, mutate, expected_missing
):
    """기본 결과 JSON은 prompts.py의 스키마와 필드 타입까지 검증한다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    data = _result()
    mutate(data)
    r = server.save_chapter_result(wid, "ch1", data)

    _check_envelope(r)
    assert r["ok"] is False
    assert expected_missing in r["data"]["missing"]
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] != "completed"


def test_save_chapter_result_requires_disabled_question_keys(tmp_path, ko_short):
    """비활성 문제 유형도 questions 키는 유지해야 한다."""
    wid = _init(
        str(ko_short), str(tmp_path / "out"), enable_reflection=False,
    )["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    data = _result()
    data["questions"].pop("reflection")
    r = server.save_chapter_result(wid, "ch1", data)

    assert r["ok"] is False
    assert "questions.reflection" in r["data"]["missing"]
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] != "completed"


def test_save_extension_result_rejects_empty_extension(tmp_path, ko_short):
    """questions.extension이 비어 있으면 거부."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    r = server.save_extension_result(wid, "ch1", {"questions": {"extension": []}})
    assert r["ok"] is False
    assert r["data"]["missing"] == ["questions.extension"]
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] != "completed"


def test_save_extension_result_rejects_disabled_extension(tmp_path, ko_short):
    wid = _init(
        str(ko_short),
        str(tmp_path / "out"),
        enable_extension=False,
    )["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    r = server.save_extension_result(wid, "ch1", _ext())

    assert r["ok"] is False
    assert r["data"]["missing"] == ["question_options.extension"]
    assert not (workspace.extension_quiz_dir(wid) / "ch1.json").exists()


@pytest.mark.parametrize(
    ("mutate", "expected_missing"),
    [
        (lambda d: d.update({"chapter_id": "ch2"}), "chapter_id"),
        (
            lambda d: d["questions"]["extension"][0].__setitem__("id", ""),
            "questions.extension[0].id",
        ),
        (
            lambda d: d["questions"]["extension"][0].__setitem__("question", " "),
            "questions.extension[0].question",
        ),
        (
            lambda d: d["questions"]["extension"][0].pop("model_answer"),
            "questions.extension[0].model_answer",
        ),
    ],
)
def test_save_extension_result_rejects_invalid_json_shape(
    tmp_path, ko_short, mutate, expected_missing
):
    """확장 결과 JSON도 extension 항목별 필드 타입을 검증한다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    data = _ext()
    mutate(data)
    r = server.save_extension_result(wid, "ch1", data)

    _check_envelope(r)
    assert r["ok"] is False
    assert expected_missing in r["data"]["missing"]
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] != "completed"


def test_save_extension_result_keeps_only_local_question_schema(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    data = _ext()
    data["questions"]["extension"][0]["legacy_extra"] = "drop me"
    r = server.save_extension_result(wid, "ch1", data)

    assert r["ok"] is True, r
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "completed"
    saved = workspace.extension_quiz_dir(wid).joinpath("ch1.json").read_text(encoding="utf-8")
    assert "legacy_extra" not in saved


def test_save_extension_result_drops_body_text(tmp_path, ko_short):
    """확장 결과에 body_text가 섞여 와도 저장 파일에는 남기지 않는다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])

    data = _ext()
    data["body_text"] = "저장하면 안 되는 원문"
    r = server.save_extension_result(wid, "ch1", data)

    assert r["ok"] is True, r
    saved = workspace.extension_quiz_dir(wid).joinpath("ch1.json").read_text(encoding="utf-8")
    assert "body_text" not in saved


# ---------------------------------------------------------------------------
# scan_pdf / scan_toc_with_ocr — 텍스트 레이어 없어도 거부하지 않고 목차 이미지 경로
# ---------------------------------------------------------------------------

def test_scan_pdf_scanned_renders_toc_images_without_ocr(tmp_path, scanned_empty):
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    r = _scan(wid)
    _check_envelope(r)
    assert r["ok"] is True, r
    rec = r["data"]["recommendations"]
    assert rec["primary_mode"] == "analyze_toc_from_images"
    assert r["data"]["toc_page_images"]
    assert r["data"]["toc_page_images"][0]["ocr_text"] == ""
    assert r["data"]["toc_page_images"][0]["ocr_error"] is None
    assert r["data"]["toc_page_images"][0]["ocr_status"] == "not_started"
    assert "prepare_ocr" in r["next_action"]
    assert "scan_toc_with_ocr" in r["next_action"]
    # 텍스트는 응답에 노출되지 않는다
    assert "scanned_text" not in r["data"]


def test_scan_toc_with_ocr_returns_ocr_text(tmp_path, scanned_empty):
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    prepared = prepare_test_ocr(wid, "korean")
    assert prepared["ok"], prepared

    r = server.scan_toc_with_ocr(wid)

    _check_envelope(r)
    assert r["ok"] is True, r
    assert r["data"]["toc_page_images"][0]["ocr_text"] == "목차 OCR 텍스트"
    assert r["data"]["toc_page_images"][0]["ocr_error"] is None
    assert r["data"]["toc_page_images"][0]["ocr_status"] == "completed"
    assert "set_chapters" in r["next_action"]
    assert r["data"]["next_step"] == {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }


def test_scan_toc_with_ocr_requires_prepare_when_cache_missing(
    tmp_path, scanned_empty, monkeypatch
):
    monkeypatch.setattr(server.analysis.ocr, "models_cached", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        server.analysis.ocr,
        "model_cache_status",
        lambda *args, **kwargs: {"cache_dir": "fake", "models": [], "all_cached": False},
    )
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    workspace.update_state(wid, ocr_language="korean")

    r = server.scan_toc_with_ocr(wid)

    _check_envelope(r)
    assert r["ok"] is False
    assert r["data"]["requires_prepare_ocr"] is True
    assert r["next_action"] == f'prepare_ocr(work_id="{wid}")'


def test_prepare_ocr_returns_model_diagnostics(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]

    r = prepare_test_ocr(wid, "korean")

    _check_envelope(r)
    assert r["ok"] is True, r
    assert r["data"]["model_loaded"] is True
    assert r["data"]["all_cached"] is True
    assert "elapsed_sec" in r["data"]


def test_scan_skips_offset_on_no_text_layer(tmp_path, scanned_empty, monkeypatch):
    """스캔본(no_text_layer)에선 offset 측정을 건너뛴다."""
    from pdf_study import analysis

    calls = {"offset": 0}
    orig = analysis.reader.detect_page_offset

    def counted(*a, **k):
        calls["offset"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(analysis.reader, "detect_page_offset", counted)
    wid = _init(str(scanned_empty), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    st = workspace.load_state(wid)
    assert st["text_quality"] == "no_text_layer"
    assert "language" not in st
    assert st["page_offset"] is None
    assert st["page_offset_confidence"] == "none"
    assert calls["offset"] == 0  # detect_page_offset 자체가 호출되지 않음


def test_scan_pdf_outline_routes_to_from_outline(tmp_path, ko_with_toc):
    wid = _init(str(ko_with_toc), str(tmp_path / "out"))["data"]["work_id"]
    r = _scan(wid)
    assert r["ok"], r
    rec = r["data"]["recommendations"]
    assert rec["primary_mode"] == "from_outline"
    assert r["data"]["outline_present"] is True
    assert [c["pdf_pages"][0] for c in rec["suggested_chapters"]] == [5, 13, 21]


def test_get_chapter_content_for_unknown_chapter(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    r = server.get_chapter_content(wid, "ch999")
    _check_envelope(r)
    assert r["ok"] is False
    assert "FileNotFoundError" in r["error"] or "not found" in r["error"]


def test_unknown_work_id_returns_ok_false(tmp_path):
    r = server.get_work_state("never-issued")
    _check_envelope(r)
    assert r["ok"] is False


def test_html_launch_scripts_finalize_response(tmp_path, ko_short):
    """init → scan → set → save → finalize 까지 의도된 응답 형식과 상태 전이."""
    r1 = _init(str(ko_short), str(tmp_path / "out"))
    _check_envelope(r1); assert r1["ok"]
    wid = r1["data"]["work_id"]

    r2 = _scan(wid)
    _check_envelope(r2); assert r2["ok"]
    # ko_short은 내장 목차가 없어 목차 OCR 경로 — 챕터는 직접 구성
    chs = [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}]

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
    assert "language" not in r5["data"]
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
    r10 = finalize_test_study(wid, "html")
    _check_envelope(r10); assert r10["ok"], r10
    out = tmp_path / "out"
    assert (out / "main.html").exists()
    assert not (out / "index.html").exists()

    assert r10["data"]["launch_command"].startswith("cd ")
    assert "study_html.py" in r10["data"]["launch_command"]
    assert r10["data"]["python"] == sys.executable
    assert r10["data"]["entry_page"] == "main.html"
    assert r10["data"]["default_url"] == "http://localhost:8765/main.html"
    assert r10["data"]["launch_scripts"] == {
        "macos_linux": "start_study.sh",
        "windows": "start_study.bat",
    }
    assert r10["data"]["auto_port_on_script_launch"] is True
    assert "start_study.sh" in r10["next_action"]
    assert "start_study.bat" in r10["next_action"]
    assert "자동으로 선택" in r10["next_action"]
    assert "Ctrl+C" in r10["next_action"]


def test_pending_guidance_tracks_each_remaining_result_kind(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 6]},
        {"chapter_id": "ch2", "title": "B", "pdf_pages": [7, 12]},
    ])

    saved_summary = server.save_chapter_result(wid, "ch1", _result())
    assert saved_summary["ok"], saved_summary
    assert "save_extension_result" in saved_summary["next_action"]
    assert "save_chapter_result" not in saved_summary["next_action"]

    saved_extension = server.save_extension_result(wid, "ch2", _ext())
    assert saved_extension["ok"], saved_extension
    assert "save_chapter_result" in saved_extension["next_action"]
    assert "save_extension_result" not in saved_extension["next_action"]

    ch1 = server.get_chapter_content(wid, "ch1")
    assert "save_extension_result" in ch1["next_action"]
    assert "save_chapter_result" not in ch1["next_action"]

    ch2 = server.get_chapter_content(wid, "ch2")
    assert "save_chapter_result" in ch2["next_action"]
    assert "save_extension_result" not in ch2["next_action"]

    completed_ch1 = server.save_extension_result(wid, "ch1", _ext())
    assert "list_pending_chapters" in completed_ch1["next_action"]
    assert "save_chapter_result" not in completed_ch1["next_action"]
    assert "save_extension_result" not in completed_ch1["next_action"]

    completed_ch2 = server.save_chapter_result(wid, "ch2", _result())
    assert "list_pending_chapters" in completed_ch2["next_action"]
    assert "save_chapter_result" not in completed_ch2["next_action"]
    assert "save_extension_result" not in completed_ch2["next_action"]


def test_pending_guidance_lists_summary_and_extension_separately_on_resume(
    tmp_path, ko_short,
):
    out = tmp_path / "result" / "ko_short"
    wid = _init(str(ko_short), str(out))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 6]},
        {"chapter_id": "ch2", "title": "B", "pdf_pages": [7, 12]},
    ])
    server.save_chapter_result(wid, "ch1", _result())
    server.save_extension_result(wid, "ch2", _ext())

    pending = server.list_pending_chapters(wid)
    assert pending["data"]["summary_pending"] == ["ch2"]
    assert pending["data"]["extension_pending"] == ["ch1"]
    assert "summary_pending=['ch2']" in pending["next_action"]
    assert "extension_pending=['ch1']" in pending["next_action"]

    workspace._registry.clear()
    resumed = resume_test_work(ko_short, tmp_path)
    assert resumed["data"]["summary_pending"] == ["ch2"]
    assert resumed["data"]["extension_pending"] == ["ch1"]
    assert "summary_pending=['ch2']" in resumed["next_action"]
    assert "extension_pending=['ch1']" in resumed["next_action"]


def test_choice_next_steps_require_only_non_choice_parameters(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    scanned = _scan(wid)

    scan_step = scanned["data"]["next_step"]
    processing_step = server._set_chapters_next_step("normal")
    finalize_step = server._finalize_next_step()
    ocr_step = server._prepare_ocr_next_step()

    assert scan_step == {
        "tool": "prepare_ocr",
        "required_parameters": [],
    }
    assert processing_step == {
        "tool": "set_chapters",
        "required_parameters": ["chapters"],
    }
    assert finalize_step == {
        "tool": "finalize_study",
        "required_parameters": [],
    }
    assert ocr_step == {
        "tool": "prepare_ocr",
        "required_parameters": [],
    }
    _assert_no_choice_fallback([
        scan_step, processing_step, finalize_step, ocr_step,
    ])


def test_completed_pending_exposes_choice_free_finalize_step(tmp_path, ko_short):
    wid = _init(
        str(ko_short),
        str(tmp_path / "out"),
        enable_short_answer=True,
        enable_reflection=True,
        enable_extension=False,
    )["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    server.save_chapter_result(wid, "ch1", _result())

    pending = server.list_pending_chapters(wid)

    assert pending["ok"], pending
    next_step = pending["data"]["next_step"]
    assert next_step == {
        "tool": "finalize_study",
        "required_parameters": [],
    }
    _assert_no_choice_fallback(pending)


def test_pending_before_chapter_setup_does_not_offer_finalize_choices(
    tmp_path, ko_short,
):
    """챕터를 확정하기 전의 빈 pending은 렌더링 가능 상태가 아니다."""
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]

    pending = server.list_pending_chapters(wid)

    assert pending["ok"], pending
    assert "next_step" not in pending["data"]


def test_completed_resume_exposes_choice_free_finalize_step(tmp_path, ko_short):
    out = tmp_path / "result" / "ko_short"
    wid = _init(
        str(ko_short),
        str(out),
        enable_short_answer=True,
        enable_reflection=True,
        enable_extension=False,
    )["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    server.save_chapter_result(wid, "ch1", _result())
    workspace._registry.clear()

    resumed = resume_test_work(ko_short, tmp_path)

    assert resumed["ok"], resumed
    assert resumed["data"]["next_step"] == {
        "tool": "finalize_study",
        "required_parameters": [],
    }
    _assert_no_choice_fallback(resumed)


def test_cleanup_work_removes_completed_workspace_without_rerender(
    tmp_path, ko_short, monkeypatch,
):
    """완료 작업의 .work만 지우며 이미 설치한 결과를 다시 렌더하지 않는다."""
    out = tmp_path / "out"
    wid = _init(
        str(ko_short),
        str(out),
        enable_short_answer=True,
        enable_reflection=True,
        enable_extension=False,
    )["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    server.save_chapter_result(wid, "ch1", _result())
    finalized = finalize_test_study(wid, "html")
    assert finalized["ok"], finalized
    assert finalized["data"]["cleanup_work"] == {
        "tool": "cleanup_work",
        "required_parameters": [],
        "desc": "최종 결과는 유지하고 이 작업의 .work 중간 데이터만 삭제합니다.",
    }
    main_before = (out / "main.html").read_bytes()
    manifest_before = (out / ".pdf-study-manifest.json").read_bytes()

    def should_not_render(*args, **kwargs):
        pytest.fail("cleanup_work must not render output again")

    monkeypatch.setattr(server, "install_rendered_output", should_not_render)

    cleaned = cleanup_test_work(wid)

    assert cleaned["ok"], cleaned
    assert cleaned["data"] == {
        "work_id": wid,
        "output_dir": str(out),
        "work_dir_deleted": True,
    }
    assert not (out / ".work").exists()
    assert (out / "main.html").read_bytes() == main_before
    assert (out / ".pdf-study-manifest.json").read_bytes() == manifest_before
    with pytest.raises(KeyError, match="unknown work_id"):
        workspace.get_work_dir(wid)


def test_cleanup_work_rejects_unrendered_workspace(tmp_path, ko_short):
    """최종 결과가 없는 작업은 .work를 지워 재개 불가능하게 만들 수 없다."""
    out = tmp_path / "out"
    wid = _init(str(ko_short), str(out))["data"]["work_id"]

    cleaned = cleanup_test_work(wid)

    assert cleaned["ok"] is False
    assert "finalize_study" in cleaned["error"]
    assert (out / ".work" / "state.json").exists()


def test_finalize_rejects_unknown_format(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    server.save_chapter_result(wid, "ch1", _result())
    r = finalize_test_study(wid, "bogus")
    _check_envelope(r)
    assert r["ok"] is False
    assert "지원하지 않는 출력 형식" in r["error"]
    assert r["data"] is None
    _assert_no_choice_fallback(r)


def test_ocr_language_setup_is_private_elicitation_data():
    setup = server._ocr_language_setup()

    assert setup["field"] == "ocr_language"
    assert [choice["value"] for choice in setup["choices"]] == [
        "korean", "english",
    ]
    _assert_no_choice_fallback(setup)


def test_md_tui_renderer_finalizes_ok(tmp_path, ko_short):
    wid = _init(str(ko_short), str(tmp_path / "out"))["data"]["work_id"]
    _scan(wid)
    _sc(wid, [{"chapter_id": "ch1", "title": "전체", "pdf_pages": [1, 12]}])
    server.save_chapter_result(wid, "ch1", _result())
    r = finalize_test_study(wid, "md_tui")
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


def test_finalize_renders_completed_chapters_and_reports_failed_results(tmp_path, ko_short):
    """실패 결과는 빠진 이유를 알리고 완료분만 최종 자료로 만든다."""
    wid = _init(str(ko_short), str(tmp_path / "out"),
                           enable_extension=False)["data"]["work_id"]
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 6]},
        {"chapter_id": "ch2", "title": "B", "pdf_pages": [7, 12]},
    ])
    server.save_chapter_result(wid, "ch1", _result())
    workspace.update_chapter_status(
        wid, "ch2", summary_status="failed", error="요약 저장이 실패했습니다.",
    )

    rendered = finalize_test_study(wid, "html")

    _check_envelope(rendered); assert rendered["ok"], rendered
    assert rendered["data"]["omitted_chapters"] == [{
        "chapter_id": "ch2",
        "results": [{
            "type": "summary",
            "status": "failed",
            "error": "요약 저장이 실패했습니다.",
        }],
    }]
    assert "ch2" in rendered["next_action"]
    assert "failed" in rendered["next_action"]
    assert (tmp_path / "out" / "index.html").exists()


def test_finalize_study_no_longer_accepts_force_argument():
    assert "force" not in inspect.signature(server.finalize_study).parameters


def test_resume_work_restores_registry_after_restart(tmp_path, ko_short):
    """서버 재시작(=레지스트리 소실)을 시뮬레이션한 뒤 resume_work로 복원."""
    out = tmp_path / "result" / "ko_short"
    wid = _init(str(ko_short), str(out),
                           enable_extension=False)["data"]["work_id"]
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 6]},
        {"chapter_id": "ch2", "title": "B", "pdf_pages": [7, 12]},
    ])
    server.save_chapter_result(wid, "ch1", _result())

    # 서버 재시작 시뮬레이션: in-memory 레지스트리 초기화
    workspace._registry.clear()
    assert not server.get_work_state(wid)["ok"]  # 복원 전엔 unknown work_id

    rr = resume_test_work(ko_short, tmp_path)
    _check_envelope(rr); assert rr["ok"], rr
    assert rr["data"]["work_id"] == wid
    assert rr["data"]["summary_pending"] == ["ch2"]
    # set_chapters에서 확정된 모드가 보존된다
    assert rr["data"]["execution_mode"] == "sequential"
    assert rr["data"]["extraction_mode"] == "text"

    assert server.get_work_state(wid)["ok"]
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "completed"


def test_resume_work_restores_pending_question_setup(tmp_path, ko_short):
    out = tmp_path / "result" / "ko_short"
    wid = _init(str(ko_short), str(out))["data"]["work_id"]
    workspace._registry.clear()

    resumed = resume_test_work(ko_short, tmp_path)

    assert resumed["ok"], resumed
    assert resumed["data"]["work_id"] == wid
    assert "question_setup" not in resumed["data"]
    assert "scan_pdf" in resumed["next_action"]
    assert workspace.load_state(wid)["page_count"] is None


def test_resume_work_missing_workspace(tmp_path, ko_short):
    r = resume_test_work(ko_short, tmp_path)
    _check_envelope(r)
    assert r["ok"] is False
