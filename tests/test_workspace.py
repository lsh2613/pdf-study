"""workspace.py 단위 + 동시성 테스트."""
from __future__ import annotations

import json
import random
import threading
import time

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
    assert state["extraction_mode"] == "text"  # 기본값
    assert state["user_context"] == "학부생 대상"
    assert state["question_options"] == {
        "multiple_choice": True, "short_answer": True,
        "reflection": False, "extension": True,
    }
    assert state["chapters"] == {}
    assert state["current_phase"] == "init"
    assert (tmp_path / "out" / ".work" / "state.json").exists()


def test_all_question_types_disabled_rejected(tmp_path, fake_pdf):
    with pytest.raises(ValueError, match="at least one question type"):
        workspace.create_workspace(
            fake_pdf, tmp_path / "out",
            options={"multiple_choice": False, "short_answer": False,
                     "reflection": False, "extension": False},
        )


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
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 5]},
        {"chapter_id": "ch2", "title": "B", "page_range": [6, 10]},
    ])
    state = workspace.load_state(wid)
    assert set(state["chapters"]) == {"ch1", "ch2"}
    assert state["chapters"]["ch1"]["summary_status"] == "pending"
    assert state["chapters"]["ch1"]["extension_status"] == "pending"
    assert state["phases"]["chapter_setup"] == "completed"


def test_save_chapter_result_marks_completed(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 5]},
    ])
    workspace.save_chapter_result(wid, "ch1", {"chapter_id": "ch1", "summary": "x"})
    state = workspace.load_state(wid)
    assert state["chapters"]["ch1"]["summary_status"] == "completed"


def test_mark_chapter_failed_increments_retry(tmp_path, fake_pdf):
    wid = workspace.create_workspace(
        fake_pdf, tmp_path / "out", options={"multiple_choice": True},
    )
    workspace.set_chapters_in_state(wid, [
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 5]},
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
        {"chapter_id": "ch1", "title": "A", "page_range": [1, 5]},
        {"chapter_id": "ch2", "title": "B", "page_range": [6, 10]},
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
        {"chapter_id": f"ch{i}", "title": f"t{i}", "page_range": [i, i + 1]}
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
