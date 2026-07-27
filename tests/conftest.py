"""pytest 공용 설정 + fixture.

- PDF fixture가 없으면 첫 실행 시 자동 빌드 (build_fixtures.build_all).
- ko_with_toc / ko_short / scanned_empty 경로를 fixture로 노출.
- tmp_workspace는 매 테스트에 임시 output_dir + 자동 register 도우미를 제공.
"""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from .fixtures.build_fixtures import FIXTURES_DIR, ensure_fixtures
from pdf_study import question_contract, server, workspace


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


class ElicitationContext:
    """등록된 MCP 도구를 테스트에서 직접 실행하기 위한 form 응답 context."""

    def __init__(self, *, cwd: Path | None = None, responses=None, supported=True):
        workspaces = {} if cwd is None else {str(cwd): {"has_changes": False}}
        self.request_context = SimpleNamespace(
            meta={"x-codex-turn-metadata": {"workspaces": workspaces}},
        )
        self.session = SimpleNamespace(
            check_client_capability=lambda _capability: supported,
        )
        self._responses = list(responses or [])
        self.messages: list[str] = []

    async def elicit(self, message, schema):
        self.messages.append(message)
        response = dict(self._responses.pop(0))
        action = response.pop("_action", "accept")
        if action != "accept":
            return SimpleNamespace(action=action, data=None)
        return SimpleNamespace(action=action, data=schema(**response))


def create_test_work(pdf_path: Path, output_dir: Path, **options) -> dict:
    """선택형 MCP를 우회하지 않고 하위 저장 primitive로 테스트 상태만 준비한다."""
    question_options = {
        "multiple_choice": options.pop("enable_multiple_choice", True),
        "short_answer": options.pop("enable_short_answer", None),
        "reflection": options.pop("enable_reflection", None),
        "extension": options.pop("enable_extension", None),
    }
    user_context = options.pop("user_context", "")
    user_context_confirmed = options.pop("_user_context_confirmed", False)
    replace_existing = options.pop("replace_existing", False)
    if options:
        raise TypeError(f"unsupported test setup options: {sorted(options)}")
    try:
        workspace.validate_workspace_inputs(
            str(pdf_path),
            question_options,
            user_context,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        return server._err(f"{type(exc).__name__}: {exc}")
    existing = workspace.inspect_output_dir(output_dir)
    if existing["kind"] != "available":
        if replace_existing and existing["kind"] in {
            "managed_work", "damaged_managed_work", "managed_output",
        }:
            workspace.replace_workspace(output_dir)
        else:
            return server._err(
                "출력 폴더가 비어 있지 않아 기존 파일을 자동으로 덮어쓰지 않았습니다.",
                data={
                    "output_dir": existing["output_dir"],
                    "existing_work": existing,
                },
            )
    work_id = workspace.create_workspace(
        pdf_path=str(pdf_path),
        output_dir=str(output_dir),
        options=question_options,
        user_context=user_context,
        user_context_confirmed=user_context_confirmed,
    )
    state = workspace.load_state(work_id)
    return server._ok({
        "work_id": work_id,
        "work_dir": str(workspace.get_work_dir(work_id)),
        "output_dir": str(output_dir),
        "question_options": state["question_options"],
    })


def scan_with_question_options(work_id: str) -> dict:
    """미정인 문제 유형을 활성화해 렌더 테스트용 스캔을 실행한다."""
    setup = server._question_setup_payload(workspace.load_state(work_id))
    selected = {
        field: True
        for field in setup["pending_fields"]
    }
    if setup["user_context_request"]:
        selected["user_context"] = ""
    return asyncio.run(
        server.scan_pdf(
            work_id=work_id,
            ctx=ElicitationContext(responses=[selected] if selected else []),
        ),
    )


def set_test_chapters(
    work_id: str,
    chapters: list[dict],
    *,
    execution_mode: str = "sequential",
    extraction_mode: str = "text",
    book_info: dict | None = None,
) -> dict:
    """등록된 set_chapters의 세 Elicitation을 승인해 테스트 챕터를 구성한다."""
    if extraction_mode == "ocr":
        state = workspace.load_state(work_id)
        if state.get("ocr_language") is None:
            # OCR 자체를 검증하는 테스트는 worker를 먼저 대역하므로, 여기서는
            # prepare_ocr의 별도 계약 대신 필요한 선행 상태만 준비한다.
            workspace.update_state(work_id, ocr_language="korean")
    return asyncio.run(server.set_chapters(
        work_id=work_id,
        chapters=chapters,
        book_info=book_info,
        ctx=ElicitationContext(responses=[
            {"chapter_strategy": "proceed", "chapters_confirmed": True},
            {"extraction_mode": extraction_mode},
            {"execution_mode": execution_mode},
        ]),
    ))


def prepare_test_ocr(work_id: str, language: str = "korean") -> dict:
    return asyncio.run(server.prepare_ocr(
        work_id=work_id,
        ctx=ElicitationContext(responses=[{"ocr_language": language}]),
    ))


def finalize_test_study(work_id: str, output_format: str) -> dict:
    return asyncio.run(server.finalize_study(
        work_id=work_id,
        ctx=ElicitationContext(responses=[{"output_format": output_format}]),
    ))


def cleanup_test_work(work_id: str) -> dict:
    return asyncio.run(server.cleanup_work(
        work_id=work_id,
        ctx=ElicitationContext(responses=[{"cleanup_confirmed": True}]),
    ))


def resume_test_work(pdf_path: Path, cwd: Path) -> dict:
    """고정 출력 경로의 기존 작업을 등록된 resume_work로 복원한다."""
    return asyncio.run(server.resume_work(
        pdf_path=str(pdf_path),
        ctx=ElicitationContext(
            cwd=cwd,
            responses=[{"resume_confirmed": True}],
        ),
    ))


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
    output_dir = tmp_path / "out"
    result = create_test_work(pdf_path, output_dir, **(options or {}))
    work_id = result["data"]["work_id"]
    scanned = scan_with_question_options(work_id)
    chapter_defs = chapters or scanned["data"]["recommendations"]["suggested_chapters"]
    configured = set_test_chapters(
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
    finalized = finalize_test_study(work_id, output_format)
    assert finalized["ok"], finalized
    return work_id, output_dir, chapter_defs
