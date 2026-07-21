"""workspace.py 단위 + 동시성 테스트."""
from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

import pytest

from pdf_study import workspace


@pytest.fixture
def fake_pdf(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return p


def test_create_workspace_initial_state(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out",
        options={"multiple_choice": True, "short_answer": True,
                 "reflection": False, "extension": True},
        user_context="학부생 대상",
        execution_mode="parallel",
    )
    state = workspace.load_state(wid)
    assert state["execution_mode"] == "parallel"
    assert state["extraction_mode"] is None  # 모드는 set_chapters에서 확정(init엔 미정)
    assert state["user_context"] == "학부생 대상"
    assert state["question_options"] == {
        "multiple_choice": True, "short_answer": True,
        "reflection": False, "extension": True,
    }
    assert state["chapters"] == {}
    assert state["current_phase"] == "init"
    assert (tmp_path / "out" / ".work" / "state.json").exists()


def test_inspect_output_dir_reports_existing_managed_work(tmp_path, fake_pdf):
    out = tmp_path / "out"
    wid = workspace.create_workspace(
        fake_pdf,
        out,
        options={"multiple_choice": True},
        work_id="existing-work",
    )

    collision = workspace.inspect_output_dir(out)

    assert collision == {
        "kind": "managed_work",
        "output_dir": str(out.resolve()),
        "work_id": wid,
        "pdf_path": str(fake_pdf.resolve()),
        "current_phase": "init",
        "can_resume": True,
    }


def test_replace_workspace_removes_only_work_dir(tmp_path, fake_pdf):
    out = tmp_path / "out"
    workspace.create_workspace(
        fake_pdf,
        out,
        options={"multiple_choice": True},
        work_id="old-work",
    )
    stale = out / ".work" / "chapters" / "summaries" / "ch1.json"
    stale.write_text('{"summary": "old"}', encoding="utf-8")
    rendered = out / "ch1.html"
    rendered.write_text("old rendered study", encoding="utf-8")
    unrelated = out / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    workspace.replace_workspace(out)

    assert not (out / ".work").exists()
    assert rendered.read_text(encoding="utf-8") == "old rendered study"
    assert unrelated.read_text(encoding="utf-8") == "keep me"


def test_inspect_output_dir_recognizes_legacy_render_without_work_dir(tmp_path):
    out = tmp_path / "legacy"
    (out / "assets").mkdir(parents=True)
    (out / "study_html.py").write_text("# launcher", encoding="utf-8")
    (out / "index.html").write_text("<html></html>", encoding="utf-8")

    collision = workspace.inspect_output_dir(out)

    assert collision["kind"] == "managed_output"
    assert collision["can_resume"] is False


def test_all_question_types_disabled_rejected(tmp_path, fake_pdf):
    with pytest.raises(ValueError, match="at least one question type"):
        workspace.create_workspace(
            fake_pdf, tmp_path / "out",
            options={"multiple_choice": False, "short_answer": False,
                     "reflection": False, "extension": False},
        )


def test_confirm_question_setup_is_atomic_and_does_not_change_confirmed_values(
    tmp_path, fake_pdf
):
    wid = workspace.create_workspace(
        fake_pdf,
        tmp_path / "out",
        options={"multiple_choice": True},
    )

    state = workspace.confirm_question_setup(
        wid,
        enable_short_answer=True,
        enable_reflection=False,
        enable_extension=True,
        user_context="  입문자  ",
    )

    assert state["question_options"] == {
        "multiple_choice": True,
        "short_answer": True,
        "reflection": False,
        "extension": True,
    }
    assert state["user_context"] == "입문자"

    with pytest.raises(ValueError, match="already confirmed"):
        workspace.confirm_question_setup(wid, enable_extension=False)
    assert workspace.load_state(wid) == state


def test_invalid_execution_mode_rejected(tmp_path, fake_pdf):
    with pytest.raises(ValueError, match="execution_mode"):
        workspace.create_workspace(
            fake_pdf, tmp_path / "out",
            options={"multiple_choice": True},
            execution_mode="bogus",
        )


def test_invalid_extraction_mode_rejected(tmp_path, fake_pdf):
    with pytest.raises(ValueError, match="extraction_mode"):
        workspace.create_workspace(
            fake_pdf, tmp_path / "out",
            options={"multiple_choice": True},
            execution_mode="sequential",
            extraction_mode="bogus",
        )


def test_missing_pdf_rejected(tmp_path):
    with pytest.raises(ValueError, match="PDF not found"):
        workspace.create_workspace(
            tmp_path / "nope.pdf", tmp_path / "out",
            options={"multiple_choice": True},
        )


def test_set_chapters_resets_status_and_phase(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
        {"chapter_id": "ch2", "title": "B", "pdf_pages": [6, 10]},
    ])
    state = workspace.load_state(wid)
    assert set(state["chapters"]) == {"ch1", "ch2"}
    assert state["chapters"]["ch1"]["summary_status"] == "pending"
    assert state["chapters"]["ch1"]["extension_status"] == "pending"
    assert state["phases"]["chapter_setup"] == "completed"


def test_set_chapters_preserves_canonical_page_metadata(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out_pages", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [{
        "chapter_id": "ch1",
        "title": "본문",
        "pdf_pages": [19, 23],
        "source_pages": [1, 5],
    }])

    chapter = workspace.load_state(wid)["chapters"]["ch1"]
    assert chapter["pdf_pages"] == [19, 23]
    assert chapter["source_pages"] == [1, 5]
    assert "page_range" not in chapter


def test_commit_chapter_setup_saves_complete_transition_once(
    tmp_path, fake_pdf, monkeypatch
):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out_atomic_setup", options={"multiple_choice": True},
    )
    original_save_state = workspace.save_state
    saved_states = []

    def record_save(work_id, state):
        saved_states.append(json.loads(json.dumps(state)))
        original_save_state(work_id, state)

    monkeypatch.setattr(workspace, "save_state", record_save)

    committed = workspace.commit_chapter_setup(
        wid,
        [{"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 1]}],
        execution_mode="parallel",
        extraction_mode="text",
        book_info={"title": "원문"},
    )

    assert len(saved_states) == 1
    assert saved_states[0] == committed
    assert committed["execution_mode"] == "parallel"
    assert committed["extraction_mode"] == "text"
    assert set(committed["chapters"]) == {"ch1"}
    assert committed["phases"]["chapter_setup"] == "completed"
    assert committed["phases"]["chapter_processing"] == "in_progress"
    assert committed["current_phase"] == "chapter_processing"
    assert workspace.load_book_info(wid) == {"title": "원문"}


def test_commit_chapter_setup_restores_book_info_when_state_save_fails(
    tmp_path, fake_pdf, monkeypatch
):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out_atomic_rollback", options={"multiple_choice": True},
    )
    workspace.save_book_info(wid, {"title": "기존 원문"})
    before_state = workspace.load_state(wid)
    book_path = workspace.book_info_path(wid)
    before_book_info = book_path.read_bytes()

    def fail_save_state(work_id, state):
        raise RuntimeError("state write failed")

    monkeypatch.setattr(workspace, "save_state", fail_save_state)

    with pytest.raises(RuntimeError, match="state write failed"):
        workspace.commit_chapter_setup(
            wid,
            [{"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 1]}],
            execution_mode="sequential",
            extraction_mode="ocr",
            book_info={"title": "새 원문"},
        )

    assert workspace.load_state(wid) == before_state
    assert book_path.read_bytes() == before_book_info


def test_commit_chapter_setup_surfaces_book_info_rollback_failure(
    tmp_path, fake_pdf, monkeypatch
):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out_atomic_rollback_error",
        options={"multiple_choice": True},
    )
    workspace.save_book_info(wid, {"title": "기존 원문"})
    book_path = workspace.book_info_path(wid)
    original_replace = workspace.os.replace
    book_replaces = 0

    def fail_rollback_replace(src, dst):
        nonlocal book_replaces
        if Path(dst) == book_path:
            book_replaces += 1
            if book_replaces == 2:
                raise OSError("rollback replace failed")
        original_replace(src, dst)

    def fail_save_state(work_id, state):
        raise RuntimeError("state write failed")

    monkeypatch.setattr(workspace.os, "replace", fail_rollback_replace)
    monkeypatch.setattr(workspace, "save_state", fail_save_state)

    with pytest.raises(RuntimeError, match="book_info rollback failed") as exc_info:
        workspace.commit_chapter_setup(
            wid,
            [{"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 1]}],
            execution_mode="sequential",
            extraction_mode="text",
            book_info={"title": "새 원문"},
        )

    assert isinstance(exc_info.value.__cause__, OSError)


def test_legacy_state_and_raw_page_keys_are_normalized_on_read(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out_legacy_pages", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [{
        "chapter_id": "ch1",
        "title": "본문",
        "pdf_pages": [19, 23],
        "source_pages": [1, 5],
    }])
    state = workspace.load_state(wid)
    state_chapter = state["chapters"]["ch1"]
    state_chapter["page_range"] = state_chapter.pop("pdf_pages")
    state_chapter["printed_range"] = state_chapter.pop("source_pages")
    workspace.save_state(wid, state)
    workspace._atomic_write_json(workspace.chapters_raw_dir(wid) / "ch1.json", {
        "chapter_id": "ch1",
        "title": "본문",
        "page_range": [19, 23],
        "printed_range": [1, 5],
        "text": "본문",
        "char_count": 2,
    })

    loaded_state = workspace.load_state(wid)["chapters"]["ch1"]
    loaded_raw = workspace.get_chapter_raw(wid, "ch1")
    for chapter in (loaded_state, loaded_raw):
        assert chapter["pdf_pages"] == [19, 23]
        assert chapter["source_pages"] == [1, 5]
        assert "page_range" not in chapter
        assert "printed_range" not in chapter


def test_save_chapter_result_marks_completed(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
    ])
    workspace.save_chapter_result(wid, "ch1", {"chapter_id": "ch1", "summary": "x"})
    state = workspace.load_state(wid)
    assert state["chapters"]["ch1"]["summary_status"] == "completed"


def test_save_chapter_result_rejects_unknown_chapter_without_files(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
    ])

    with pytest.raises(KeyError):
        workspace.save_chapter_result(wid, "ch999", {"chapter_id": "ch999"})

    assert not (workspace.summaries_dir(wid) / "ch999.json").exists()
    assert not (workspace.quiz_dir(wid) / "ch999.json").exists()
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"


def test_save_results_reject_skip_chapter_without_files_or_status_change(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "Index", "pdf_pages": [1, 1], "skip": True},
    ])

    with pytest.raises(ValueError, match="skipped"):
        workspace.save_chapter_result(wid, "ch1", {"chapter_id": "ch1"})
    with pytest.raises(ValueError, match="skipped"):
        workspace.save_extension_result(wid, "ch1", {"chapter_id": "ch1"})

    assert not (workspace.summaries_dir(wid) / "ch1.json").exists()
    assert not (workspace.quiz_dir(wid) / "ch1.json").exists()
    assert not (workspace.extension_quiz_dir(wid) / "ch1.json").exists()
    entry = workspace.load_state(wid)["chapters"]["ch1"]
    assert entry["summary_status"] == "skipped"
    assert entry["extension_status"] == "skipped"


def test_save_extension_result_rejects_unknown_chapter_without_files(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
    ])

    with pytest.raises(KeyError):
        workspace.save_extension_result(wid, "ch999", {"chapter_id": "ch999"})

    assert not (workspace.extension_quiz_dir(wid) / "ch999.json").exists()
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "pending"


def test_save_chapter_result_restores_files_when_state_save_fails(
    tmp_path, fake_pdf, monkeypatch
):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
    ])
    summary_path = workspace.summaries_dir(wid) / "ch1.json"
    quiz_path = workspace.quiz_dir(wid) / "ch1.json"

    original_save_state = workspace.save_state

    def fail_after_files(work_id, state):
        if state["chapters"]["ch1"].get("summary_status") == "completed":
            raise RuntimeError("state write failed")
        original_save_state(work_id, state)

    monkeypatch.setattr(workspace, "save_state", fail_after_files)

    with pytest.raises(RuntimeError, match="state write failed"):
        workspace.save_chapter_result(wid, "ch1", {"chapter_id": "ch1"})

    assert not summary_path.exists()
    assert not quiz_path.exists()
    assert workspace.load_state(wid)["chapters"]["ch1"]["summary_status"] == "pending"


def test_save_extension_result_restores_previous_file_when_state_save_fails(
    tmp_path, fake_pdf, monkeypatch
):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
    ])
    path = workspace.extension_quiz_dir(wid) / "ch1.json"
    path.write_text('{"keep": true}', encoding="utf-8")

    original_save_state = workspace.save_state

    def fail_after_files(work_id, state):
        if state["chapters"]["ch1"].get("extension_status") == "completed":
            raise RuntimeError("state write failed")
        original_save_state(work_id, state)

    monkeypatch.setattr(workspace, "save_state", fail_after_files)

    with pytest.raises(RuntimeError, match="state write failed"):
        workspace.save_extension_result(wid, "ch1", {"chapter_id": "ch1"})

    assert path.read_text(encoding="utf-8") == '{"keep": true}'
    assert workspace.load_state(wid)["chapters"]["ch1"]["extension_status"] == "pending"


def test_mark_chapter_failed_increments_retry(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
    ])
    workspace.mark_chapter_failed(wid, "ch1", kind="summary", error="oops")
    workspace.mark_chapter_failed(wid, "ch1", kind="summary", error="oops again")
    entry = workspace.load_state(wid)["chapters"]["ch1"]
    assert entry["summary_status"] == "failed"
    assert entry["retry_count"] == 2
    assert entry["error"] == "oops again"


def test_list_pending_chapters(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True, "extension": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "pdf_pages": [1, 5]},
        {"chapter_id": "ch2", "title": "B", "pdf_pages": [6, 10]},
    ])
    workspace.save_chapter_result(wid, "ch1", {"chapter_id": "ch1"})
    pending = workspace.list_pending_chapters_impl(wid)
    assert "ch1" not in pending["summary_pending"]
    assert "ch2" in pending["summary_pending"]
    assert {"ch1", "ch2"} == set(pending["extension_pending"])


def test_concurrent_writes_keep_state_intact(tmp_path, fake_pdf):
    """50 스레드가 동시에 update_chapter_status + save_chapter_result 호출.

    state.json이 valid JSON으로 유지되고 모든 챕터가 completed로 끝나야 한다.
    """
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    chs = [
        {"chapter_id": f"ch{i}", "title": f"t{i}", "pdf_pages": [i, i + 1]}
        for i in range(1, 11)
    ]
    workspace.set_chapters_in_state(wid, chs)

    errors: list[str] = []

    def worker(idx: int) -> None:
        try:
            time.sleep(random.uniform(0, 0.005))
            cid = f"ch{(idx % 10) + 1}"
            workspace.update_chapter_status(wid, cid, retry_count=idx)
            workspace.save_chapter_result(wid, cid, {"chapter_id": cid, "thread": idx})
        except Exception as e:
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errors, errors[:3]

    state = workspace.load_state(wid)
    # valid JSON 보장 (load_state가 raise 없이 dict 반환했으면 통과)
    assert isinstance(state, dict)
    for cid, entry in state["chapters"].items():
        assert entry["summary_status"] == "completed", (cid, entry)

    # raw 파일 차원에서도 valid JSON 인지 확인
    raw_text = (tmp_path / "out" / ".work" / "state.json").read_text(encoding="utf-8")
    json.loads(raw_text)

    # .tmp 잔여물 없음 (atomic write rename 정상)
    leftover = list((tmp_path / "out" / ".work").rglob("*.tmp"))
    assert leftover == []


def test_book_info_and_outline_io(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.save_book_info(wid, {"title": "T", "author": "A"})
    assert workspace.load_book_info(wid) == {"title": "T", "author": "A"}
    workspace.save_outline(wid, {"mode": "from_toc"})
    assert workspace.load_outline(wid) == {"mode": "from_toc"}
