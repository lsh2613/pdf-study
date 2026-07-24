"""pytest 공용 설정 + fixture.

- PDF fixture가 없으면 첫 실행 시 자동 빌드 (build_fixtures.build_all).
- ko_with_toc / ko_short / scanned_empty 경로를 fixture로 노출.
- tmp_workspace는 매 테스트에 임시 output_dir + 자동 register 도우미를 제공.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from .fixtures.build_fixtures import FIXTURES_DIR, ensure_fixtures
from pdf_study import question_contract, server


def pytest_configure(config):
    """PDF fixture가 없거나 오래되었으면 즉시 빌드.

    합성 PDF는 git에 들어가지 않으므로 첫 실행/clean 환경에서 자동으로
    생성하고, 생성기나 입력 폰트가 바뀌면 기존 파일을 교체한다.
    """
    ensure_fixtures()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def ko_with_toc(fixtures_dir) -> Path:
    return fixtures_dir / "ko_with_toc.pdf"


@pytest.fixture(scope="session")
def ko_short(fixtures_dir) -> Path:
    return fixtures_dir / "ko_short.pdf"


@pytest.fixture(scope="session")
def scanned_empty(fixtures_dir) -> Path:
    return fixtures_dir / "scanned_empty.pdf"


def scan_with_question_options(work_id: str) -> dict:
    """미정인 문제 유형을 활성화해 렌더 테스트용 스캔을 실행한다."""
    options = server.workspace.load_state(work_id)["question_options"]
    return server._scan_pdf_impl(
        work_id,
        enable_short_answer=True if options.get("short_answer") is None else None,
        enable_reflection=True if options.get("reflection") is None else None,
        enable_extension=True if options.get("extension") is None else None,
    )


def fake_summary_result(
    chapter_id: str,
    *,
    multiple_choice: bool = True,
    short_answer: bool = True,
    reflection: bool = True,
    answer_index: int = 0,
    question: str = "?",
    model_answer: str = "ans",
) -> dict:
    result = copy.deepcopy(question_contract.summary_payload_example())
    result.update(
        chapter_id=chapter_id,
        title=f"제목 {chapter_id}",
        summary="본문 요약 내용입니다.",
        key_points=["p1", "p2"],
    )
    questions = result["questions"]
    questions["multiple_choice"][0].update(
        id=f"{chapter_id}_mc",
        question=question,
        options=["A", "B"],
        answer_index=answer_index,
        explanation="해설",
    )
    questions["short_answer"][0].update(
        id=f"{chapter_id}_sa", question=question, model_answer=model_answer,
    )
    questions["reflection"][0].update(
        id=f"{chapter_id}_rf", question=question, model_answer=model_answer,
    )
    if not multiple_choice:
        questions["multiple_choice"] = []
    if not short_answer:
        questions["short_answer"] = []
    if not reflection:
        questions["reflection"] = []
    return result


def fake_extension_result(
    chapter_id: str,
    *,
    question: str = "?",
    model_answer: str = "ans",
) -> dict:
    result = copy.deepcopy(question_contract.extension_payload_example())
    result["chapter_id"] = chapter_id
    result["questions"]["extension"][0].update(
        id=f"{chapter_id}_ex", question=question, model_answer=model_answer,
    )
    return result


def build_rendered_study(
    pdf_path: Path,
    tmp_path: Path,
    output_format: str,
    *,
    chapters: list[dict] | None = None,
    options: dict | None = None,
    book_info: dict | None = None,
    summary_kwargs: dict | None = None,
) -> tuple[str, Path, list[dict]]:
    """실제 MCP 저장 흐름을 거쳐 지정한 형식의 렌더 결과를 만든다."""
    result = server._init_work_impl(str(pdf_path), str(tmp_path / "out"), **(options or {}))
    work_id = result["data"]["work_id"]
    scanned = scan_with_question_options(work_id)
    chapter_defs = chapters or scanned["data"]["recommendations"]["suggested_chapters"]
    configured = server._set_chapters_impl(
        work_id,
        chapter_defs,
        execution_mode="sequential",
        extraction_mode="text",
        book_info=book_info or {"title": "테스트 책", "author": "T"},
    )
    assert configured["ok"], configured
    for chapter in chapter_defs:
        chapter_id = chapter["chapter_id"]
        saved = server.save_chapter_result(
            work_id, chapter_id, fake_summary_result(chapter_id, **(summary_kwargs or {})),
        )
        assert saved["ok"], saved
        if server.get_subagent_prompts(work_id)["data"]["enabled_types"]["extension"]:
            extension = server.save_extension_result(
                work_id,
                chapter_id,
                fake_extension_result(
                    chapter_id,
                    question=(summary_kwargs or {}).get("question", "?"),
                    model_answer=(summary_kwargs or {}).get("model_answer", "ans"),
                ),
            )
            assert extension["ok"], extension
    finalized = server._finalize_study_impl(work_id, output_format)
    assert finalized["ok"], finalized
    return work_id, tmp_path / "out", chapter_defs
