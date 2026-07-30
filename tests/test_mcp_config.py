"""MCP client configuration safety tests."""
from __future__ import annotations

import asyncio
import inspect
import json
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pdf_study import server
from scripts import apply_mcp_config


ROOT = Path(__file__).resolve().parent.parent


def _mcp_input_properties() -> dict[str, set[str]]:
    tools = asyncio.run(server.mcp.list_tools())
    return {
        tool.name: set(tool.inputSchema["properties"])
        for tool in tools
    }


def test_init_and_resume_public_schemas_exclude_choice_and_path_parameters():
    properties = _mcp_input_properties()

    assert properties["init_work"] == {"pdf_path"}
    assert properties["resume_work"] == {"pdf_path"}
    assert properties["list_study_results"] == set()


def test_choice_tools_expose_only_non_choice_public_parameters():
    properties = _mcp_input_properties()

    assert properties["scan_pdf"] == {"work_id", "scan_size", "force_vision"}
    assert properties["prepare_ocr"] == {"work_id"}
    assert properties["set_chapters"] == {"work_id", "chapters", "book_info"}
    assert properties["finalize_study"] == {"work_id"}
    assert properties["cleanup_work"] == {"work_id"}


def test_choice_workflows_have_only_elicitation_python_entrypoints():
    choice_workflows = {
        "init_work",
        "resume_work",
        "scan_pdf",
        "prepare_ocr",
        "set_chapters",
        "finalize_study",
        "cleanup_work",
    }

    assert choice_workflows <= vars(server).keys()
    assert choice_workflows <= set(_mcp_input_properties())
    for name in choice_workflows:
        entrypoint = vars(server)[name]
        assert inspect.iscoroutinefunction(entrypoint)
        assert "ctx" in inspect.signature(entrypoint).parameters
        assert not hasattr(server, f"_{name}_impl")
        assert not hasattr(server, f"_mcp_{name}_tool")


def test_global_config_uses_home_dir_not_project_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    home_dir = tmp_path / "home"
    project_dir.mkdir()
    home_dir.mkdir()

    paths = apply_mcp_config.apply_configs(
        command=str(project_dir / ".venv/bin/python"),
        cache_dir=project_dir / ".paddleocr",
        project_dir=project_dir,
        scope="global",
        targets=["claude"],
        home_dir=home_dir,
    )

    assert paths == [home_dir / ".claude.json"]
    assert (home_dir / ".claude.json").exists()
    assert not (project_dir / ".claude.json").exists()


def test_antigravity_cli_uses_current_global_mcp_config_path(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    home_dir = tmp_path / "home"
    project_dir.mkdir()
    home_dir.mkdir()

    paths = apply_mcp_config.apply_configs(
        command=str(project_dir / ".venv/bin/python"),
        cache_dir=project_dir / ".paddleocr",
        project_dir=project_dir,
        scope="global",
        targets=["antigravity-cli"],
        home_dir=home_dir,
    )

    expected = home_dir / ".gemini/config/mcp_config.json"
    assert paths == [expected]
    assert expected.exists()
    assert not (home_dir / ".gemini/antigravity-cli/mcp_config.json").exists()


def test_config_paths_rejects_project_local_scope(tmp_path: Path) -> None:
    with pytest.raises(apply_mcp_config.ConfigError, match="global"):
        apply_mcp_config.config_paths("local", tmp_path)


def test_invalid_existing_config_fails_without_modifying_any_target(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    home_dir = tmp_path / "home"
    project_dir.mkdir()
    home_dir.mkdir()
    claude_config = home_dir / ".claude.json"
    claude_config.write_text('{"mcpServers":', encoding="utf-8")

    with pytest.raises(apply_mcp_config.ConfigError, match="invalid JSON"):
        apply_mcp_config.apply_configs(
            command=str(project_dir / ".venv/bin/python"),
            cache_dir=project_dir / ".paddleocr",
            project_dir=project_dir,
            scope="global",
            targets=["claude", "antigravity-cli"],
            home_dir=home_dir,
        )

    assert claude_config.read_text(encoding="utf-8") == '{"mcpServers":'
    assert not (home_dir / ".gemini/config/mcp_config.json").exists()


def test_existing_config_is_backed_up_before_atomic_replacement(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    home_dir = tmp_path / "home"
    project_dir.mkdir()
    home_dir.mkdir()
    claude_config = home_dir / ".claude.json"
    original = '{"globalMcpServers": {"other": {"command": "other"}}}\n'
    claude_config.write_text(original, encoding="utf-8")

    apply_mcp_config.apply_configs(
        command=str(project_dir / ".venv/bin/python"),
        cache_dir=project_dir / ".paddleocr",
        project_dir=project_dir,
        scope="global",
        targets=["claude"],
        home_dir=home_dir,
    )

    assert (home_dir / ".claude.json.pdf-study.bak").read_text(encoding="utf-8") == original
    data = json.loads(claude_config.read_text(encoding="utf-8"))
    assert set(data["globalMcpServers"]) == {"other", "pdf-study"}
    assert not list(home_dir.glob("..claude.json.*.tmp"))


def test_gitignore_covers_local_mcp_config_paths() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".claude.json" in gitignore
    assert ".agents/mcp_config.json" in gitignore
    assert "*.pdf-study.bak" in gitignore


def test_global_codex_registration_uses_cli_and_verifies_result(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex는 JSON 파일 대신 공식 CLI 등록과 조회로 설정을 확정한다."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="pdf-study", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    apply_mcp_config.apply_codex_cli_config(
        command=str(tmp_path / ".venv/bin/python"),
        cache_dir=tmp_path / ".paddleocr",
        codex_bin="codex",
    )

    assert calls == [
        [
            "codex", "mcp", "add", "--env",
            f"PDF_STUDY_PADDLEOCR_CACHE={tmp_path / '.paddleocr'}",
            "pdf-study", "--", str(tmp_path / ".venv/bin/python"),
            "-m", "pdf_study",
        ],
        ["codex", "mcp", "get", "pdf-study"],
    ]


def test_codex_never_policy_becomes_mcp_elicitation_only(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = (
        "# Preserve this comment.\n"
        'approval_policy = "never" # Existing automation default.\n'
        'model = "gpt-test"\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "other"\n'
    )
    config.write_text(original, encoding="utf-8")
    config.chmod(0o640)

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is True
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["approval_policy"] == {
        "granular": {
            "sandbox_approval": False,
            "rules": False,
            "mcp_elicitations": True,
            "request_permissions": False,
            "skill_approval": False,
        },
    }
    assert parsed["model"] == "gpt-test"
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert "# Preserve this comment." in config.read_text(encoding="utf-8")
    assert "# Existing automation default." in config.read_text(encoding="utf-8")
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert config.with_name("config.toml.pdf-study.bak").read_text(
        encoding="utf-8"
    ) == original


def test_codex_missing_policy_becomes_mcp_elicitation_only(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        'model = "gpt-test"\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "other"\n'
    )
    config.write_text(original, encoding="utf-8")

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is True
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["approval_policy"] == {
        "granular": {
            "sandbox_approval": False,
            "rules": False,
            "mcp_elicitations": True,
            "request_permissions": False,
            "skill_approval": False,
        },
    }
    assert parsed["model"] == "gpt-test"
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert config.with_name("config.toml.pdf-study.bak").read_text(
        encoding="utf-8"
    ) == original


@pytest.mark.parametrize("policy", ["on-request", "untrusted"])
def test_codex_interactive_policy_is_left_unchanged(
    tmp_path: Path, policy: str,
) -> None:
    config = tmp_path / "config.toml"
    original = f'approval_policy = "{policy}"\n'
    config.write_text(original, encoding="utf-8")

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is False
    assert config.read_text(encoding="utf-8") == original
    assert not config.with_name("config.toml.pdf-study.bak").exists()


def test_codex_compatible_granular_policy_is_left_unchanged(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        "approval_policy = { granular = { "
        "mcp_elicitations = true, sandbox_approval = false } }\n"
    )
    config.write_text(original, encoding="utf-8")

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is False
    assert config.read_text(encoding="utf-8") == original


def test_codex_explicitly_disabled_granular_elicitation_fails_closed(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        "approval_policy = { granular = { "
        "mcp_elicitations = false, sandbox_approval = false } }\n"
    )
    config.write_text(original, encoding="utf-8")

    with pytest.raises(
        apply_mcp_config.ConfigError,
        match="explicitly does not allow mcp_elicitations",
    ):
        apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert config.read_text(encoding="utf-8") == original
    assert not config.with_name("config.toml.pdf-study.bak").exists()


def test_config_cli_updates_never_policy_before_codex_registration(
    monkeypatch, tmp_path: Path, capsys,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text('approval_policy = "never"\n', encoding="utf-8")
    registration_calls = []

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(
        apply_mcp_config.shutil,
        "which",
        lambda name: "/usr/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        apply_mcp_config,
        "apply_codex_cli_config",
        lambda **kwargs: registration_calls.append(kwargs),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_mcp_config.py",
            "--command",
            str(tmp_path / ".venv/bin/python"),
            "--cache-dir",
            str(tmp_path / ".paddleocr"),
            "--scope",
            "global",
            "--project-dir",
            str(tmp_path),
            "codex",
        ],
    )

    assert apply_mcp_config.main() == 0

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["approval_policy"]["granular"]["mcp_elicitations"] is True
    assert len(registration_calls) == 1
    output = capsys.readouterr().out
    assert "Updated Codex approval policy" in output
    assert "--dangerously-bypass-approvals-and-sandbox" in output
    assert "codex --sandbox danger-full-access" in output
