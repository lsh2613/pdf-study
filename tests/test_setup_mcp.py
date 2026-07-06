"""MCP 전용 venv 설치 스크립트와 가이드 문서 검증."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "setup_mcp.sh"
GUIDE = ROOT / "docs" / "10-mcp-setup.md"


def test_setup_script_prints_absolute_mcp_config():
    """--print-config는 현재 checkout 기준 절대경로 MCP config를 출력해야 한다."""
    bash = shutil.which("bash")
    assert bash, "bash is required to validate setup_mcp.sh"

    subprocess.run([bash, "-n", str(SCRIPT)], check=True)
    out = subprocess.check_output(
        [bash, str(SCRIPT), "--print-config"],
        text=True,
        cwd=ROOT,
    )
    data = json.loads(out)
    cfg = data["mcpServers"]["pdf-study"]
    command = Path(cfg["command"])

    assert command.is_absolute()
    assert command == ROOT / ".venv" / "bin" / "python"
    assert cfg["args"] == ["-m", "pdf_study"]
    assert cfg["env"] == {
        "PDF_STUDY_PADDLEOCR_CACHE": str(ROOT / ".paddleocr"),
    }
    assert "<PDF_STUDY_INSTALL_DIR>" not in out
    assert "~/" not in out


def test_mcp_setup_guide_documents_local_venv_install():
    """가이드는 프로젝트 로컬 .venv와 기본 자동 적용 흐름을 안내해야 한다."""
    text = GUIDE.read_text(encoding="utf-8")
    assert "scripts/setup_mcp.sh" in text
    assert "<PDF_STUDY_INSTALL_DIR>/.venv/bin/python" in text
    assert "전역 Python" in text
    assert "Claude Code, Codex CLI, Antigravity CLI 설정을 자동 적용" in text
    assert "대상 클라이언트를 지정하지 않으면 세 클라이언트 설정을 모두 적용" in text
    assert "--print-config" in text
    assert "설치, import 검증, 클라이언트 설정 적용을 하지 않고" in text
    assert "--check" in text
    assert "기존 `.venv`에서 필수 import가 가능한지만 확인" in text
    assert "uv" in text
    assert "libomp" in text

    docs_index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "10-mcp-setup.md" in docs_index

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "scripts/setup_mcp.sh" in readme
    assert "설정을 자동 적용" in readme


def test_setup_script_checks_paddle_runtime_dependencies():
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"paddle": "paddlepaddle"' in text
    assert '"paddleocr": "paddleocr"' in text
    assert "PDF_STUDY_PADDLEOCR_CACHE" in text
