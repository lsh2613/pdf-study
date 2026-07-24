"""MCP 요청 컨텍스트 기반 cwd와 사용자 선택 강제 계약."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session

from pdf_study import server, workspace


class _ElicitationContext:
    def __init__(
        self,
        *,
        cwd=None,
        responses: list[dict] | None = None,
        elicitation_supported: bool = True,
    ):
        workspaces = {} if cwd is None else {str(cwd): {"has_changes": False}}
        self.request_context = SimpleNamespace(
            meta={
                "x-codex-turn-metadata": {
                    "workspaces": workspaces,
                },
            },
        )
        self.session = SimpleNamespace(
            check_client_capability=lambda capability: elicitation_supported,
        )
        self._responses = list(responses or [])
        self.messages: list[str] = []

    async def elicit(self, message, schema):
        self.messages.append(message)
        data = schema(**self._responses.pop(0))
        return SimpleNamespace(action="accept", data=data)


def test_sync_init_work_never_falls_back_to_mcp_server_cwd(
    tmp_path, ko_short, monkeypatch,
):
    server_cwd = tmp_path / "server-cwd"
    server_cwd.mkdir()
    monkeypatch.chdir(server_cwd)

    response = server.init_work(str(ko_short), "")

    assert response["ok"] is False
    assert response["data"]["required_parameters"] == ["output_dir"]
    assert not (server_cwd / "result").exists()


def test_mcp_init_work_uses_single_codex_workspace_as_agent_cwd(
    tmp_path, ko_short, monkeypatch,
):
    agent_cwd = tmp_path / "agent-cwd"
    server_cwd = tmp_path / "server-cwd"
    agent_cwd.mkdir()
    server_cwd.mkdir()
    monkeypatch.chdir(server_cwd)
    ctx = _ElicitationContext(cwd=agent_cwd, elicitation_supported=False)

    response = asyncio.run(
        server._mcp_init_work_tool(
            pdf_path=str(ko_short),
            output_dir="",
            enable_short_answer=False,
            enable_reflection=False,
            enable_extension=False,
            ctx=ctx,
        )
    )

    expected = agent_cwd / "result" / ko_short.stem
    assert response["ok"] is True, response
    assert response["data"]["output_dir"] == str(expected)
    assert expected.is_dir()
    assert not (server_cwd / "result").exists()


def test_mcp_init_work_uses_elicited_question_choices_not_agent_arguments(
    tmp_path, ko_short,
):
    ctx = _ElicitationContext(
        cwd=tmp_path,
        responses=[{
            "output_dir_confirmed": True,
            "enable_short_answer": False,
            "enable_reflection": True,
            "enable_extension": False,
        }],
    )

    response = asyncio.run(
        server._mcp_init_work_tool(
            pdf_path=str(ko_short),
            enable_short_answer=True,
            enable_reflection=False,
            enable_extension=True,
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


def test_mcp_init_work_requires_confirmation_of_resolved_output_dir(
    tmp_path, ko_short,
):
    ctx = _ElicitationContext(
        cwd=tmp_path,
        responses=[{
            "output_dir_confirmed": False,
            "enable_short_answer": False,
            "enable_reflection": False,
            "enable_extension": False,
        }],
    )

    response = asyncio.run(
        server._mcp_init_work_tool(pdf_path=str(ko_short), ctx=ctx)
    )

    assert response["ok"] is False
    assert not (tmp_path / "result" / "ko_short").exists()
    assert str(tmp_path / "result" / "ko_short") in ctx.messages[0]


def test_mcp_init_work_does_not_replace_existing_work_without_elicited_confirmation(
    tmp_path, ko_short,
):
    output_dir = tmp_path / "existing"
    original = server.init_work(
        str(ko_short),
        str(output_dir),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    original_work_id = original["data"]["work_id"]
    ctx = _ElicitationContext(responses=[{"replace_existing": False}])

    response = asyncio.run(
        server._mcp_init_work_tool(
            pdf_path=str(ko_short),
            output_dir=str(output_dir),
            enable_short_answer=False,
            enable_reflection=False,
            enable_extension=False,
            replace_existing=True,
            ctx=ctx,
        )
    )

    assert response["ok"] is False
    assert workspace.resume_workspace(output_dir)["work_id"] == original_work_id
    assert "기존 작업 교체" in ctx.messages[0]
    assert "이전 렌더 결과는 새 렌더가 성공할 때까지 유지됩니다." in ctx.messages[0]


def test_mcp_resume_work_requires_elicited_confirmation(
    tmp_path, ko_short, monkeypatch,
):
    output_dir = tmp_path / "resume"
    created = server.init_work(str(ko_short), str(output_dir))
    assert created["ok"] is True
    called = []
    original_resume = server.resume_work

    def recording_resume(*args, **kwargs):
        called.append((args, kwargs))
        return original_resume(*args, **kwargs)

    monkeypatch.setattr(server, "resume_work", recording_resume)
    ctx = _ElicitationContext(responses=[{"resume_confirmed": False}])

    response = asyncio.run(
        server._mcp_resume_work_tool(output_dir=str(output_dir), ctx=ctx)
    )

    assert response["ok"] is False
    assert called == []
    assert "기존 작업 이어가기" in ctx.messages[0]
    assert "기존 .work/state.json을 등록해 남은 챕터부터 계속합니다." in ctx.messages[0]


def test_mcp_scan_pdf_uses_elicited_question_choices_not_agent_arguments(
    tmp_path, ko_short,
):
    initialized = server.init_work(str(ko_short), str(tmp_path / "out"))
    work_id = initialized["data"]["work_id"]
    ctx = _ElicitationContext(
        responses=[{
            "enable_short_answer": False,
            "enable_reflection": True,
            "enable_extension": False,
        }],
    )

    response = asyncio.run(
        server._mcp_scan_pdf_tool(
            work_id=work_id,
            enable_short_answer=True,
            enable_reflection=False,
            enable_extension=True,
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


def test_mcp_set_chapters_uses_elicited_mode_and_confirms_chapters(
    tmp_path, ko_short,
):
    initialized = server.init_work(
        str(ko_short),
        str(tmp_path / "out"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    scanned = server.scan_pdf(work_id)
    assert scanned["ok"] is True, scanned
    ctx = _ElicitationContext(
        responses=[{
            "chapter_strategy": "proceed",
            "processing_mode": "parallel:text",
            "chapters_confirmed": True,
        }],
    )

    response = asyncio.run(
        server._mcp_set_chapters_tool(
            work_id=work_id,
            chapters=[
                {
                    "chapter_id": "ch1",
                    "title": "사용자 확인 대상",
                    "pdf_pages": [1, 12],
                },
            ],
            execution_mode="sequential",
            extraction_mode="text",
            ctx=ctx,
        )
    )

    assert response["ok"] is True, response
    state = workspace.load_state(work_id)
    assert state["execution_mode"] == "parallel"
    assert state["extraction_mode"] == "text"
    assert "사용자 확인 대상" in ctx.messages[0]
    assert "Sequential + Text" in ctx.messages[0]
    assert "Parallel + Text" in ctx.messages[0]


def test_mcp_set_chapters_honors_reanalyze_choice_without_changing_state(
    tmp_path, ko_with_toc,
):
    initialized = server.init_work(
        str(ko_with_toc),
        str(tmp_path / "out-reanalyze"),
        enable_short_answer=False,
        enable_reflection=False,
        enable_extension=False,
    )
    work_id = initialized["data"]["work_id"]
    scanned = server.scan_pdf(work_id)
    assert scanned["ok"] is True, scanned
    assert scanned["data"]["recommendations"]["primary_mode"] == "from_outline"
    ctx = _ElicitationContext(
        responses=[{
            "chapter_strategy": "reanalyze_with_vision",
            "processing_mode": "sequential:text",
            "chapters_confirmed": False,
        }],
    )

    response = asyncio.run(
        server._mcp_set_chapters_tool(
            work_id=work_id,
            chapters=scanned["data"]["recommendations"]["suggested_chapters"],
            execution_mode="parallel",
            extraction_mode="ocr",
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


def test_mcp_finalize_uses_elicited_format_not_agent_argument(monkeypatch):
    captured = {}

    def fake_finalize(work_id, output_format="", keep_work_dir=True):
        captured.update(
            work_id=work_id,
            output_format=output_format,
            keep_work_dir=keep_work_dir,
        )
        return server._ok({"format": output_format})

    monkeypatch.setattr(server, "finalize_study", fake_finalize)
    ctx = _ElicitationContext(responses=[{"output_format": "md_tui"}])

    response = asyncio.run(
        server._mcp_finalize_study_tool(
            work_id="work-1",
            output_format="html",
            keep_work_dir=False,
            ctx=ctx,
        )
    )

    assert response["ok"] is True
    assert captured == {
        "work_id": "work-1",
        "output_format": "md_tui",
        "keep_work_dir": False,
    }
    assert "정적 웹사이트 — 브라우저로 열람 + 진도 저장 서버" in ctx.messages[0]
    assert "챕터별 Markdown + 터미널 학습 TUI" in ctx.messages[0]


def test_mcp_cleanup_requires_elicited_confirmation(monkeypatch):
    called = []

    def fake_cleanup(work_id):
        called.append(work_id)
        return server._ok({"work_id": work_id})

    monkeypatch.setattr(server, "cleanup_work", fake_cleanup)
    ctx = _ElicitationContext(responses=[{"cleanup_confirmed": False}])

    response = asyncio.run(
        server._mcp_cleanup_work_tool(work_id="work-1", ctx=ctx)
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
                "output_dir_confirmed": True,
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
                    "enable_short_answer": True,
                    "enable_reflection": False,
                    "enable_extension": True,
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
