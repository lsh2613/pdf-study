"""MCP client configuration safety tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "apply_mcp_config.py"


def run_apply(project_dir: Path, *targets: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--command",
            str(project_dir / ".venv/bin/python"),
            "--cache-dir",
            str(project_dir / ".paddleocr"),
            "--scope",
            "local",
            "--project-dir",
            str(project_dir),
            *targets,
        ],
        cwd=cwd or project_dir,
        text=True,
        capture_output=True,
    )


def test_local_config_uses_repository_dir_not_callers_working_dir(tmp_path: Path) -> None:
    repo_dir = tmp_path / "cloned-repository"
    caller_dir = tmp_path / "caller"
    repo_dir.mkdir()
    caller_dir.mkdir()

    result = run_apply(repo_dir, "claude", cwd=caller_dir)

    assert result.returncode == 0, result.stderr
    assert (repo_dir / ".claude.json").exists()
    assert not (caller_dir / ".claude.json").exists()


def test_invalid_existing_config_fails_without_modifying_any_target(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repository"
    repo_dir.mkdir()
    claude_config = repo_dir / ".claude.json"
    claude_config.write_text('{"mcpServers":', encoding="utf-8")

    result = run_apply(repo_dir, "claude", "codex")

    assert result.returncode != 0
    assert "invalid JSON" in result.stderr
    assert claude_config.read_text(encoding="utf-8") == '{"mcpServers":'
    assert not (repo_dir / ".codex/mcp.json").exists()


def test_existing_config_is_backed_up_before_atomic_replacement(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repository"
    codex_dir = repo_dir / ".codex"
    codex_dir.mkdir(parents=True)
    codex_config = codex_dir / "mcp.json"
    original = '{"mcpServers": {"other": {"command": "other"}}}\n'
    codex_config.write_text(original, encoding="utf-8")

    result = run_apply(repo_dir, "codex")

    assert result.returncode == 0, result.stderr
    assert (codex_dir / "mcp.json.pdf-study.bak").read_text(encoding="utf-8") == original
    data = json.loads(codex_config.read_text(encoding="utf-8"))
    assert set(data["mcpServers"]) == {"other", "pdf-study"}
    assert not list(codex_dir.glob(".mcp.json.*.tmp"))


def test_gitignore_covers_local_mcp_config_paths() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".claude.json" in gitignore
    assert ".codex/mcp.json" in gitignore
    assert ".agents/mcp_config.json" in gitignore
    assert "*.pdf-study.bak" in gitignore
