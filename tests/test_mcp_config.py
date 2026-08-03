"""MCP client configuration safety tests."""
from __future__ import annotations

import asyncio
import inspect
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pdf_learner import server
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
    assert properties["get_chapter_summary"] == {"work_id", "chapter_id"}


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


def test_global_codex_registration_uses_cli_and_verifies_result(
    monkeypatch, tmp_path: Path
) -> None:
    """Codex는 JSON 파일 대신 공식 CLI 등록과 조회로 설정을 확정한다."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="pdf-learner", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    apply_mcp_config.apply_codex_cli_config(
        command=str(tmp_path / ".venv/bin/python"),
        cache_dir=tmp_path / ".paddleocr",
        codex_bin="codex",
    )

    assert calls == [
        [
            "codex", "mcp", "add", "--env",
            f"PDF_LEARNER_PADDLEOCR_CACHE={tmp_path / '.paddleocr'}",
            "pdf-learner", "--", str(tmp_path / ".venv/bin/python"),
            "-m", "pdf_learner",
        ],
        ["codex", "mcp", "get", "pdf-learner"],
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
        "granular": apply_mcp_config.GRANULAR_APPROVALS_ENABLED,
    }
    assert parsed["model"] == "gpt-test"
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert "# Preserve this comment." in config.read_text(encoding="utf-8")
    assert "# Existing automation default." in config.read_text(encoding="utf-8")
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert config.with_name("config.toml.pdf-learner.bak").read_text(
        encoding="utf-8"
    ) == original
    assert "[approval_policy.granular]\nsandbox_approval = true" in config.read_text(
        encoding="utf-8"
    )


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
        "granular": apply_mcp_config.GRANULAR_APPROVALS_ENABLED,
    }
    assert parsed["model"] == "gpt-test"
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert config.with_name("config.toml.pdf-learner.bak").read_text(
        encoding="utf-8"
    ) == original


def test_codex_missing_config_creates_minimal_elicitation_policy(tmp_path: Path) -> None:
    config = tmp_path / "codex-home" / "config.toml"

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is True
    assert tomllib.loads(config.read_text(encoding="utf-8")) == {
        "approval_policy": {
            "granular": apply_mcp_config.GRANULAR_APPROVALS_ENABLED,
        },
    }
    assert not config.with_name("config.toml.pdf-learner.bak").exists()


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
    assert not config.with_name("config.toml.pdf-learner.bak").exists()


def test_codex_enabled_granular_policy_is_left_unchanged(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        "approval_policy = { granular = { "
        "sandbox_approval = true, rules = true, mcp_elicitations = true, "
        "request_permissions = true, skill_approval = true } }\n"
    )
    config.write_text(original, encoding="utf-8")

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is False
    assert config.read_text(encoding="utf-8") == original


def test_codex_granular_policy_enables_every_approval_category(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        "approval_policy = { granular = { "
        "mcp_elicitations = false, sandbox_approval = false } }\n"
    )
    config.write_text(original, encoding="utf-8")

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is True
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["approval_policy"]["granular"] == apply_mcp_config.GRANULAR_APPROVALS_ENABLED
    assert config.with_name("config.toml.pdf-learner.bak").read_text(
        encoding="utf-8"
    ) == original


def test_codex_granular_policy_replaces_missing_categories_with_enabled_values(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        "[approval_policy.granular]\n"
        "rules = true\n"
        "sandbox_approval = false\n"
    )
    config.write_text(original, encoding="utf-8")

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is True
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["approval_policy"]["granular"] == apply_mcp_config.GRANULAR_APPROVALS_ENABLED
    assert config.with_name("config.toml.pdf-learner.bak").read_text(
        encoding="utf-8"
    ) == original


def test_codex_repairs_known_multiline_granular_policy_to_minimal_table(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        'model = "gpt-test"\n\n'
        "approval_policy = { granular = {\n"
        "  mcp_elicitations = true,\n"
        "  rules = true,\n"
        "  sandbox_approval = false\n"
        "} }\n\n"
        "[tui]\n"
        "status_line_use_colors = true\n"
    )
    config.write_text(original, encoding="utf-8")

    changed = apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert changed is True
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["approval_policy"] == {
        "granular": apply_mcp_config.GRANULAR_APPROVALS_ENABLED,
    }
    assert parsed["model"] == "gpt-test"
    assert parsed["tui"]["status_line_use_colors"] is True
    assert config.with_name("config.toml.pdf-learner.bak").read_text(
        encoding="utf-8"
    ) == original


def test_codex_unknown_invalid_toml_is_not_modified(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = 'model = "gpt-test"\ninvalid = {\n'
    config.write_text(original, encoding="utf-8")

    with pytest.raises(apply_mcp_config.ConfigError, match="invalid TOML"):
        apply_mcp_config.ensure_codex_elicitation_allowed(config)

    assert config.read_text(encoding="utf-8") == original
    assert not config.with_name("config.toml.pdf-learner.bak").exists()


def test_codex_pdf_learner_tools_default_to_automatic_approval(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "gpt-test"\n\n'
        "[mcp_servers.pdf-learner]\n"
        'command = "/tmp/pdf-learner-python"\n\n'
        "[mcp_servers.pdf-learner.tools.init_work]\n"
        'approval_mode = "approve"\n\n'
        "[mcp_servers.other]\n"
        'command = "other"\n',
        encoding="utf-8",
    )

    changed = apply_mcp_config.ensure_codex_pdf_learner_tools_auto_approved(config)

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert changed is True
    assert parsed["mcp_servers"]["pdf-learner"]["default_tools_approval_mode"] == "approve"
    assert parsed["mcp_servers"]["pdf-learner"]["tools"]["init_work"]["approval_mode"] == "approve"
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert config.with_name("config.toml.pdf-learner.bak").exists()


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
    def fake_codex_registration(**kwargs: object) -> None:
        registration_calls.append(kwargs)
        config.write_text(
            config.read_text(encoding="utf-8")
            + "\n[mcp_servers.pdf-learner]\n"
            + 'command = "/tmp/pdf-learner-python"\n',
            encoding="utf-8",
        )

    monkeypatch.setattr(
        apply_mcp_config,
        "apply_codex_cli_config",
        fake_codex_registration,
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
        ],
    )

    assert apply_mcp_config.main() == 0

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["approval_policy"]["granular"]["mcp_elicitations"] is True
    assert parsed["mcp_servers"]["pdf-learner"]["default_tools_approval_mode"] == "approve"
    assert len(registration_calls) == 1
    output = capsys.readouterr().out
    assert "Updated Codex approval policy" in output
    assert "--dangerously-bypass-approvals-and-sandbox" in output
    assert "codex --sandbox danger-full-access" in output
