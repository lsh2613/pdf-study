"""MCP 요청 컨텍스트 기반 cwd와 사용자 선택 강제 계약."""
from __future__ import annotations

import asyncio

from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from pdf_study import server, workspace
from .conftest import (
    create_test_work,
    ElicitationContext,
    scan_with_question_options,
)


def _assert_no_choice_fallback(value):
    if isinstance(value, dict):
        for forbidden in (
            "choices",
            "user_choice_required",
            "user_choice_instruction",
            "user_choice_options",
            "user_choices",
            "question_setup",
            "ocr_language_setup",
        ):
            assert forbidden not in value
        for nested in value.values():
            _assert_no_choice_fallback(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_choice_fallback(nested)
    elif isinstance(value, str):
        assert "[선택지 제시 규칙]" not in value
        assert "① 이대로 진행" not in value


def _assert_no_removed_workflow_inputs(value) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _assert_no_removed_workflow_inputs(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_removed_workflow_inputs(nested)
    elif isinstance(value, str):
        for removed in (
            "output_dir",
            "output_format",
            "extraction_mode",
            "execution_mode",
        ):
            assert removed not in value


def test_choice_tools_fail_closed_without_elicitation(tmp_path, ko_short):
    ctx = ElicitationContext(
        cwd=tmp_path,
        supported=False,
    )
    calls = [
        server.init_work(pdf_path=str(ko_short), ctx=ctx),
        server.resume_work(pdf_path=str(ko_short), ctx=ctx),
        server.scan_pdf(work_id="missing", ctx=ctx),
        server.prepare_ocr(work_id="missing", ctx=ctx),
        server.set_chapters(
            work_id="missing",
            chapters=[],
            ctx=ctx,
        ),
        server.finalize_study(work_id="missing", ctx=ctx),
        server.cleanup_work(work_id="missing", ctx=ctx),
    ]

    responses = [asyncio.run(call) for call in calls]

    assert all(response["ok"] is False for response in responses)
    assert all(
        response["data"] == {
            "required_capability": "elicitation.form",
        }
        for response in responses
    )
    assert ctx.messages == []
    assert not (tmp_path / "result").exists()


def test_init_work_never_falls_back_to_mcp_server_cwd(
    tmp_path, ko_short, monkeypatch,
):
    server_cwd = tmp_path / "server-cwd"
    server_cwd.mkdir()
    monkeypatch.chdir(server_cwd)

    response = asyncio.run(server.init_work(
        pdf_path=str(ko_short),
        ctx=ElicitationContext(),
    ))

    assert response["ok"] is False
    assert response["data"]["required_context"] == ["workspace_or_root"]
    assert "required_parameters" not in response["data"]
    _assert_no_removed_workflow_inputs(response["error"])
    _assert_no_removed_workflow_inputs(response["next_action"])
    assert not (server_cwd / "result").exists()


def test_choice_tool_descriptions_never_advertise_removed_inputs():
    choice_tool_names = {
        "init_work",
        "resume_work",
        "scan_pdf",
        "prepare_ocr",
        "set_chapters",
        "finalize_study",
        "cleanup_work",
    }

    for tool in asyncio.run(server.mcp.list_tools()):
        if tool.name in choice_tool_names:
            _assert_no_removed_workflow_inputs(tool.description)


def test_public_recovery_guidance_uses_registered_tool_inputs(
    tmp_path, ko_short, monkeypatch,
):
    initialized = create_test_work(
        str(ko_short),
        str(tmp_path / "out"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]

    scanned = scan_with_question_options(work_id)
    assert scanned["ok"] is True, scanned
    pending = server.get_subagent_prompts(work_id)
    _assert_no_choice_fallback(pending)
    _assert_no_removed_workflow_inputs(pending)

    monkeypatch.setattr(
        server.analysis,
        "prepare_ocr_impl",
        lambda _work_id, _language: {},
    )
    prepared = asyncio.run(server.prepare_ocr(
        work_id=work_id,
        ctx=ElicitationContext(responses=[{"ocr_language": "korean"}]),
    ))
    _assert_no_removed_workflow_inputs(prepared["next_action"])

    finalize_guidance = server._pending_guidance(
        {
            "phases": {"chapter_setup": "completed"},
            "chapters": {},
            "question_options": {"extension": False},
        },
        work_id,
    )
    _assert_no_removed_workflow_inputs(finalize_guidance)


def test_scan_toc_with_ocr_filters_private_choice_data(
    tmp_path, ko_short, monkeypatch,
):
    initialized = create_test_work(
        str(ko_short),
        str(tmp_path / "out"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    workspace.update_state(work_id, ocr_language="korean")
    monkeypatch.setattr(
        server.analysis,
        "scan_toc_with_ocr_impl",
        lambda _work_id: {
            "toc_page_images": [],
            "recommendations": {
                "user_choice_options": [{"value": "proceed"}],
                "next_step_guidance": "set_chapters(work_id, chapters=...)",
            },
        },
    )

    response = server.scan_toc_with_ocr(work_id)

    assert response["ok"] is True
    _assert_no_choice_fallback(response)
    _assert_no_removed_workflow_inputs(response)


def test_mcp_init_work_uses_single_codex_workspace_as_agent_cwd(
    tmp_path, ko_short, monkeypatch,
):
    agent_cwd = tmp_path / "agent-cwd"
    server_cwd = tmp_path / "server-cwd"
    agent_cwd.mkdir()
    server_cwd.mkdir()
    monkeypatch.chdir(server_cwd)
    ctx = ElicitationContext(
        cwd=agent_cwd,
        responses=[{
            "enable_short_answer": False,
            "enable_reflection": False,
            "enable_extension": False,
        }],
    )

    response = asyncio.run(
        server.init_work(
            pdf_path=str(ko_short),
            ctx=ctx,
        )
    )

    expected = agent_cwd / "result" / ko_short.stem
    assert response["ok"] is True, response
    assert response["data"]["output_dir"] == str(expected)
    assert expected.is_dir()
    assert not (server_cwd / "result").exists()
    _assert_no_choice_fallback(response)


def test_mcp_init_work_uses_elicited_question_choices(
    tmp_path, ko_short,
):
    ctx = ElicitationContext(
        cwd=tmp_path,
        responses=[{
            "enable_short_answer": False,
            "enable_reflection": True,
            "enable_extension": False,
        }],
    )

    response = asyncio.run(
        server.init_work(
            pdf_path=str(ko_short),
            ctx=ctx,
        )
    )

    assert response["ok"] is True, response
    assert response["data"]["question_options"] == {
        "multiple_choice": True,
        "short_answer": False,
        "reflection": True,
        "extension": False,
    }
    assert len(ctx.messages) == 1


def test_mcp_init_work_allows_omitted_user_context(
    tmp_path, ko_short,
):
    ctx = ElicitationContext(
        cwd=tmp_path,
        responses=[{
            "enable_short_answer": False,
            "enable_reflection": False,
            "enable_extension": False,
        }],
    )

    response = asyncio.run(
        server.init_work(pdf_path=str(ko_short), ctx=ctx)
    )

    assert response["ok"] is True
    assert workspace.load_state(response["data"]["work_id"])["user_context"] == ""
    assert str(tmp_path / "result" / "ko_short") in ctx.messages[0]


def test_mcp_init_work_uses_elicited_user_context(tmp_path, ko_short):
    ctx = ElicitationContext(
        cwd=tmp_path,
        responses=[{
            "enable_short_answer": False,
            "enable_reflection": False,
            "enable_extension": False,
            "user_context": "  입문자  ",
        }],
    )

    response = asyncio.run(
        server.init_work(pdf_path=str(ko_short), ctx=ctx)
    )

    assert response["ok"] is True
    assert (
        workspace.load_state(response["data"]["work_id"])["user_context"]
        == "입문자"
    )


def test_mcp_init_work_elicits_resume_for_existing_work(
    tmp_path, ko_short,
):
    output_dir = tmp_path / "result" / ko_short.stem
    original = create_test_work(
        str(ko_short),
        str(output_dir),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    original_work_id = original["data"]["work_id"]
    ctx = ElicitationContext(
        cwd=tmp_path,
        responses=[{"action": "resume"}],
    )

    response = asyncio.run(
        server.init_work(
            pdf_path=str(ko_short),
            ctx=ctx,
        )
    )

    assert response["ok"] is True
    assert response["data"]["work_id"] == original_work_id
    assert len(ctx.messages) == 1
    assert "기존 작업 이어가기" in ctx.messages[0]
    assert "기존 작업 교체" in ctx.messages[0]


def test_mcp_init_work_elicits_replace_for_existing_work(tmp_path, ko_short):
    output_dir = tmp_path / "result" / ko_short.stem
    original = create_test_work(
        str(ko_short),
        str(output_dir),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    assert original["ok"] is True
    ctx = ElicitationContext(
        cwd=tmp_path,
        responses=[
            {"action": "replace"},
            {
                "enable_short_answer": False,
                "enable_reflection": False,
                "enable_extension": False,
                "user_context": "교체된 작업",
            },
        ],
    )

    response = asyncio.run(
        server.init_work(pdf_path=str(ko_short), ctx=ctx)
    )

    assert response["ok"] is True
    assert (
        workspace.load_state(response["data"]["work_id"])["user_context"]
        == "교체된 작업"
    )
    assert response["data"]["output_dir"] == str(output_dir)
    assert len(ctx.messages) == 2


def test_mcp_resume_work_requires_elicited_confirmation(
    tmp_path, ko_short, monkeypatch,
):
    output_dir = tmp_path / "result" / ko_short.stem
    created = create_test_work(str(ko_short), str(output_dir))
    assert created["ok"] is True
    called = []
    original_resume = workspace.resume_workspace

    def recording_resume(*args, **kwargs):
        called.append((args, kwargs))
        return original_resume(*args, **kwargs)

    monkeypatch.setattr(workspace, "resume_workspace", recording_resume)
    ctx = ElicitationContext(
        cwd=tmp_path,
        responses=[{"resume_confirmed": False}],
    )

    response = asyncio.run(
        server.resume_work(pdf_path=str(ko_short), ctx=ctx)
    )

    assert response["ok"] is False
    assert called == []
    assert "기존 작업 이어가기" in ctx.messages[0]
    assert "기존 .work/state.json을 등록해 남은 챕터부터 계속합니다." in ctx.messages[0]


def test_mcp_scan_pdf_uses_elicited_question_choices(
    tmp_path, ko_short,
):
    initialized = create_test_work(str(ko_short), str(tmp_path / "out"))
    work_id = initialized["data"]["work_id"]
    ctx = ElicitationContext(
        responses=[{
            "enable_short_answer": False,
            "enable_reflection": True,
            "enable_extension": False,
        }],
    )

    response = asyncio.run(
        server.scan_pdf(
            work_id=work_id,
            ctx=ctx,
        )
    )

    assert response["ok"] is True, response
    assert workspace.load_state(work_id)["question_options"] == {
        "multiple_choice": True,
        "short_answer": False,
        "reflection": True,
        "extension": False,
    }
    assert "단답형 문제를 생성할까요?" in ctx.messages[0]
    assert "단답형 문제 포함" in ctx.messages[0]
    assert "챕터 핵심 개념을 짧은 문장으로 답하는 문제를 만듭니다." in ctx.messages[0]
    _assert_no_choice_fallback(response)
    _assert_no_removed_workflow_inputs(response)


def test_mcp_set_chapters_uses_elicited_mode_and_confirms_chapters(
    tmp_path, ko_short,
):
    initialized = create_test_work(
        str(ko_short),
        str(tmp_path / "out"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    scanned = scan_with_question_options(work_id)
    assert scanned["ok"] is True, scanned
    ctx = ElicitationContext(
        responses=[
            {
                "chapter_strategy": "proceed",
                "chapters_confirmed": True,
            },
            {"extraction_mode": "text"},
            {"execution_mode": "parallel"},
        ],
    )

    response = asyncio.run(
        server.set_chapters(
            work_id=work_id,
            chapters=[
                {
                    "chapter_id": "ch1",
                    "title": "사용자 확인 대상",
                    "pdf_pages": [1, 12],
                },
            ],
            ctx=ctx,
        )
    )

    assert response["ok"] is True, response
    state = workspace.load_state(work_id)
    assert state["execution_mode"] == "parallel"
    assert state["extraction_mode"] == "text"
    assert len(ctx.messages) == 3
    assert "사용자 확인 대상" in ctx.messages[0]
    assert "[챕터 구성과 범위]" in ctx.messages[0]
    assert "[본문 추출 방식]" not in ctx.messages[0]
    assert "[본문 추출 방식]" in ctx.messages[1]
    assert "Text" in ctx.messages[1]
    assert "OCR" in ctx.messages[1]
    assert "Sequential" not in ctx.messages[1]
    assert "[실행 방식]" in ctx.messages[2]
    assert "Sequential" in ctx.messages[2]
    assert "Parallel" in ctx.messages[2]
    assert "OCR" not in ctx.messages[2]


@pytest.mark.parametrize(
    ("responses", "message_count"),
    [
        ([{"_action": "cancel"}], 1),
        (
            [
                {"chapter_strategy": "proceed", "chapters_confirmed": True},
                {"_action": "decline"},
            ],
            2,
        ),
        (
            [
                {"chapter_strategy": "proceed", "chapters_confirmed": True},
                {"extraction_mode": "text"},
                {"_action": "cancel"},
            ],
            3,
        ),
    ],
)
def test_mcp_set_chapters_cancellation_never_changes_state(
    responses, message_count, tmp_path, ko_short,
):
    initialized = create_test_work(
        str(ko_short),
        str(tmp_path / "out"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    scanned = scan_with_question_options(work_id)
    ctx = ElicitationContext(responses=responses)

    response = asyncio.run(
        server.set_chapters(
            work_id=work_id,
            chapters=scanned["data"]["recommendations"]["suggested_chapters"],
            ctx=ctx,
        )
    )

    assert response["ok"] is False
    assert len(ctx.messages) == message_count
    assert workspace.load_state(work_id)["phases"]["chapter_setup"] != "completed"
    _assert_no_choice_fallback(response)


def test_mcp_extraction_elicitation_forces_ocr_for_garbled_text(
    tmp_path, ko_short,
):
    initialized = create_test_work(
        str(ko_short),
        str(tmp_path / "ocr-only"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    workspace.update_state(work_id, text_quality="garbled")
    ctx = ElicitationContext(responses=[{"extraction_mode": "ocr"}])

    selected = asyncio.run(server._elicit_extraction_mode(ctx, work_id))

    assert selected == "ocr"
    assert "OCR" in ctx.messages[0]
    assert "PDF 텍스트 레이어를 사용" not in ctx.messages[0]

    invalid_ctx = ElicitationContext(responses=[{"extraction_mode": "text"}])
    with pytest.raises(ValueError):
        asyncio.run(server._elicit_extraction_mode(invalid_ctx, work_id))


def test_mcp_set_chapters_honors_reanalyze_choice_without_changing_state(
    tmp_path, ko_with_toc,
):
    initialized = create_test_work(
        str(ko_with_toc),
        str(tmp_path / "out-reanalyze"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    scanned = scan_with_question_options(work_id)
    assert scanned["ok"] is True, scanned
    assert scanned["data"]["recommendations"]["primary_mode"] == "from_outline"
    ctx = ElicitationContext(
        responses=[{
            "chapter_strategy": "reanalyze_with_vision",
            "chapters_confirmed": False,
        }],
    )

    response = asyncio.run(
        server.set_chapters(
            work_id=work_id,
            chapters=scanned["data"]["recommendations"]["suggested_chapters"],
            ctx=ctx,
        )
    )

    assert response["ok"] is False
    assert "force_vision=True" in response["next_action"]
    assert workspace.load_state(work_id)["phases"]["chapter_setup"] != "completed"
    assert "목차 이미지로 재분석" in ctx.messages[0]
    assert (
        "목차 페이지를 렌더한 뒤 OCR 텍스트와 이미지로 챕터를 다시 구성합니다."
        in ctx.messages[0]
    )
    assert len(ctx.messages) == 1


def test_mcp_finalize_uses_elicited_format(monkeypatch, tmp_path, ko_short):
    captured = {}

    class FakeRenderer:
        def render(self, work_id, output_dir):
            captured["render"] = (work_id, output_dir)

    initialized = create_test_work(ko_short, tmp_path / "out")
    work_id = initialized["data"]["work_id"]
    monkeypatch.setitem(server.RENDERERS, "md_tui", FakeRenderer)
    monkeypatch.setattr(
        server,
        "install_rendered_output",
        lambda selected_work_id, output_format, render: captured.update(
            work_id=selected_work_id,
            output_format=output_format,
        ),
    )
    ctx = ElicitationContext(responses=[{"output_format": "md_tui"}])

    response = asyncio.run(
        server.finalize_study(
            work_id=work_id,
            ctx=ctx,
        )
    )

    assert response["ok"] is True
    assert captured == {
        "work_id": work_id,
        "output_format": "md_tui",
    }
    assert "정적 웹사이트 — 브라우저로 열람 + 진도 저장 서버" in ctx.messages[0]
    assert "챕터별 Markdown + 터미널 학습 TUI" in ctx.messages[0]


def test_mcp_cleanup_requires_elicited_confirmation(monkeypatch):
    called = []

    def fake_cleanup(work_id):
        called.append(work_id)
        return server._ok({"work_id": work_id})

    monkeypatch.setattr(workspace, "cleanup_workspace", fake_cleanup)
    ctx = ElicitationContext(responses=[{"cleanup_confirmed": False}])

    response = asyncio.run(
        server.cleanup_work(work_id="work-1", ctx=ctx)
    )

    assert response["ok"] is False
    assert called == []
    assert "최종 결과는 유지하고 이 작업의 .work 중간 데이터만 삭제합니다." in ctx.messages[0]


def test_fastmcp_round_trip_uses_request_workspace_and_elicitation(
    tmp_path, ko_short,
):
    messages = []

    async def on_elicit(context, params):
        messages.append(params.message)
        return types.ElicitResult(
            action="accept",
            content={
                "enable_short_answer": False,
                "enable_reflection": True,
                "enable_extension": False,
            },
        )

    async def scenario():
        async with create_connected_server_and_client_session(
            server.mcp,
            elicitation_callback=on_elicit,
        ) as client:
            initialized = await client.call_tool(
                "init_work",
                {
                    "pdf_path": str(ko_short),
                },
                meta={
                    "x-codex-turn-metadata": {
                        "workspaces": {str(tmp_path): {"has_changes": False}},
                    },
                },
            )
            init_data = initialized.structuredContent
            assert init_data is not None
            assert init_data["data"]["output_dir"] == str(
                tmp_path / "result" / "ko_short",
            )

            scanned = await client.call_tool(
                "scan_pdf",
                {
                    "work_id": init_data["data"]["work_id"],
                },
            )
            scan_data = scanned.structuredContent
            assert scan_data is not None
            return scan_data

    response = asyncio.run(scenario())

    assert response["ok"] is True, response
    assert response["data"]["question_options"] == {
        "multiple_choice": True,
        "short_answer": False,
        "reflection": True,
        "extension": False,
    }
    assert len(messages) == 1


def test_fastmcp_set_chapters_uses_three_ordered_elicitations(
    tmp_path, ko_short,
):
    messages = []

    async def on_elicit(context, params):
        messages.append(params.message)
        if "[챕터 구성과 범위]" in params.message:
            content = {
                "chapter_strategy": "proceed",
                "chapters_confirmed": True,
            }
        elif "[본문 추출 방식]" in params.message:
            content = {"extraction_mode": "text"}
        elif "[실행 방식]" in params.message:
            content = {"execution_mode": "parallel"}
        else:
            raise AssertionError(params.message)
        return types.ElicitResult(action="accept", content=content)

    initialized = create_test_work(
        str(ko_short),
        str(tmp_path / "round-trip"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    scanned = scan_with_question_options(work_id)

    async def scenario():
        async with create_connected_server_and_client_session(
            server.mcp,
            elicitation_callback=on_elicit,
        ) as client:
            return await client.call_tool(
                "set_chapters",
                {
                    "work_id": work_id,
                    "chapters": [
                        {
                            "chapter_id": "ch1",
                            "title": "통합 테스트",
                            "pdf_pages": [1, 12],
                        },
                    ],
                },
            )

    result = asyncio.run(scenario())

    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is True
    assert len(messages) == 3
    assert "[챕터 구성과 범위]" in messages[0]
    assert "[본문 추출 방식]" in messages[1]
    assert "[실행 방식]" in messages[2]


def test_fastmcp_static_choice_elicitations_use_supported_schemas(monkeypatch):
    captured = {}

    def fake_prepare_ocr(work_id, ocr_language=""):
        captured["ocr"] = (work_id, ocr_language)
        return server._ok({"ocr_language": ocr_language})

    monkeypatch.setattr(server.analysis, "prepare_ocr_impl", fake_prepare_ocr)
    monkeypatch.setattr(
        server.workspace,
        "load_state",
        lambda _work_id: {
            "output_dir": "/tmp/pdf-study-finalize-test",
            "chapters": {},
            "question_options": {"extension": False},
        },
    )
    monkeypatch.setattr(
        server.workspace,
        "get_work_dir",
        lambda _work_id: "/tmp/pdf-study-finalize-test/.work",
    )
    monkeypatch.setattr(server.workspace, "update_phase", lambda *_args: None)
    monkeypatch.setattr(
        server,
        "install_rendered_output",
        lambda work_id, output_format, _render: captured.update(
            finalize=(work_id, output_format, True),
        ),
    )
    monkeypatch.setitem(
        server.RENDERERS,
        "md_tui",
        type("FakeRenderer", (), {"render": lambda *_args: None}),
    )

    async def on_elicit(context, params):
        if "OCR로 읽을 PDF의 언어" in params.message:
            content = {"ocr_language": "english"}
        elif "최종 학습 자료 형식" in params.message:
            content = {"output_format": "md_tui"}
        else:
            raise AssertionError(params.message)
        return types.ElicitResult(action="accept", content=content)

    async def scenario():
        async with create_connected_server_and_client_session(
            server.mcp,
            elicitation_callback=on_elicit,
        ) as client:
            prepared = await client.call_tool(
                "prepare_ocr",
                {"work_id": "work-ocr"},
            )
            finalized = await client.call_tool(
                "finalize_study",
                {"work_id": "work-finalize"},
            )
            return prepared, finalized

    prepared, finalized = asyncio.run(scenario())

    assert prepared.structuredContent is not None
    assert prepared.structuredContent["ok"] is True
    assert finalized.structuredContent is not None
    assert finalized.structuredContent["ok"] is True
    assert captured == {
        "ocr": ("work-ocr", "english"),
        "finalize": ("work-finalize", "md_tui", True),
    }
