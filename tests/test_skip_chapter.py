"""skip 챕터 처리 회귀 가드.

찾아보기/색인/판권 같은 비본문 챕터가 set_chapters 시 skip=True로 표시되면
- raw 추출, sub-agent 디스패치, HTML 렌더링에서 모두 제외돼야 한다.
- summary_status / extension_status 가 "skipped"로 초기화돼야 한다.
- list_pending_chapters는 skipped를 pending으로 보지 않아야 한다.
- get_subagent_prompts.chapter_ids에서 빠지고 skipped_chapter_ids로 분리돼야 한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_study import server, workspace
from pdf_study import analysis


def _setup(tmp_path, pdf):
    r = server.init_work(str(pdf), str(tmp_path / "out"))
    return r["data"]["work_id"], tmp_path / "out"


def _scan(wid):
    return server.scan_pdf(
        wid,
        enable_short_answer=True,
        enable_reflection=True,
        enable_extension=True,
    )


def _sc(wid, chapters):
    """set_chapters 호출 — 모드는 본문 처리용 기본값으로 고정."""
    return server.set_chapters(wid, chapters,
                               execution_mode="sequential", extraction_mode="text")


def test_skip_marks_status_skipped(tmp_path, ko_with_toc):
    wid, _ = _setup(tmp_path, ko_with_toc)
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "본문", "page_range": [5, 12]},
        {"chapter_id": "ch2", "title": "찾아보기", "page_range": [27, 28], "skip": True},
    ])
    state = workspace.load_state(wid)
    assert state["chapters"]["ch1"]["summary_status"] == "pending"
    assert state["chapters"]["ch1"].get("skip") is False
    assert state["chapters"]["ch2"]["summary_status"] == "skipped"
    assert state["chapters"]["ch2"]["extension_status"] == "skipped"
    assert state["chapters"]["ch2"]["skip"] is True


def test_skip_excluded_from_pending(tmp_path, ko_with_toc):
    wid, _ = _setup(tmp_path, ko_with_toc)
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "본문", "page_range": [5, 12]},
        {"chapter_id": "ch2", "title": "찾아보기", "page_range": [27, 28], "skip": True},
    ])
    pending = server.list_pending_chapters(wid)
    assert "ch1" in pending["data"]["summary_pending"]
    assert "ch2" not in pending["data"]["summary_pending"]
    assert "ch2" not in pending["data"]["extension_pending"]


def test_skip_excluded_from_subagent_chapter_ids(tmp_path, ko_with_toc):
    wid, _ = _setup(tmp_path, ko_with_toc)
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "본문 1", "page_range": [5, 12]},
        {"chapter_id": "ch2", "title": "본문 2", "page_range": [13, 20]},
        {"chapter_id": "ch3", "title": "찾아보기", "page_range": [27, 28], "skip": True},
    ])
    r = server.get_subagent_prompts(wid)
    data = r["data"]
    assert data["chapter_ids"] == ["ch1", "ch2"]
    assert data["skipped_chapter_ids"] == ["ch3"]


def test_skip_skips_raw_extraction(tmp_path, ko_with_toc):
    wid, out = _setup(tmp_path, ko_with_toc)
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "본문", "page_range": [5, 12]},
        {"chapter_id": "ch2", "title": "찾아보기", "page_range": [27, 28], "skip": True},
    ])
    # ch1은 raw 파일 생김, ch2는 안 생김
    raw_dir = out / ".work/raw_data/chapters_raw"
    assert (raw_dir / "ch1.json").exists()
    assert not (raw_dir / "ch2.json").exists()


def test_skip_chapter_not_rendered(tmp_path, ko_with_toc):
    wid, out = _setup(tmp_path, ko_with_toc)
    _scan(wid)
    _sc(wid, [
        {"chapter_id": "ch1", "title": "본문 1", "page_range": [5, 12]},
        {"chapter_id": "ch2", "title": "본문 2", "page_range": [13, 20]},
        {"chapter_id": "ch3", "title": "찾아보기", "page_range": [27, 28], "skip": True},
    ])
    # 가짜 결과 저장 — ch1/ch2만
    for cid in ("ch1", "ch2"):
        server.save_chapter_result(wid, cid, {
            "chapter_id": cid, "title": "t",
            "summary": "x", "key_points": [],
            "questions": {"multiple_choice": [], "short_answer": [], "reflection": []},
        })
    # extension 미완료 상태로 의도적으로 렌더 → force로 완료 가드 우회
    fin = server.finalize_study(wid, "html", force=True)
    assert fin["ok"], fin

    # 멀티 챕터로 렌더 (skip 제외 후에도 2개 남음)
    assert (out / "index.html").exists()
    assert (out / "ch1.html").exists()
    assert (out / "ch2.html").exists()
    # 찾아보기 챕터 페이지는 생성되지 않음
    assert not (out / "ch3.html").exists()

    idx = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-chapter="ch1"' in idx
    assert 'data-chapter="ch2"' in idx
    assert 'data-chapter="ch3"' not in idx
    assert "찾아보기" not in idx

    # 사이드바도 마찬가지
    ch1 = (out / "ch1.html").read_text(encoding="utf-8")
    assert 'data-chapter="ch3"' not in ch1
    assert "찾아보기" not in ch1


def test_set_chapters_impl_marks_skipped_in_result(tmp_path, ko_with_toc):
    """analysis.set_chapters_impl 응답에 skipped 챕터가 표시되는지."""
    wid, _ = _setup(tmp_path, ko_with_toc)
    _scan(wid)
    result = analysis.set_chapters_impl(wid, [
        {"chapter_id": "ch1", "title": "본문", "page_range": [5, 12]},
        {"chapter_id": "ch2", "title": "찾아보기", "page_range": [27, 28], "skip": True},
    ], "sequential", "text")
    by_id = {c["chapter_id"]: c for c in result["chapters"]}
    assert by_id["ch1"].get("skipped") is not True
    assert by_id["ch2"].get("skipped") is True
    assert by_id["ch2"]["char_count"] == 0
