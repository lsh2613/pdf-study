"""MCP client configuration safety tests."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import apply_mcp_config


ROOT = Path(__file__).resolve().parent.parent


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
    assert not (home_dir / ".gemini/antigravity-cli/mcp_config.json").exists()


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
