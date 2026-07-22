"""MCP 전용 venv 설치 스크립트와 가이드 문서 검증."""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
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


def test_mcp_setup_guide_is_a_short_entry_to_operations():
    """설치 진입 문서는 중복 없이 운영 절차의 설치 절로 연결해야 한다."""
    text = GUIDE.read_text(encoding="utf-8")
    assert "## 빠른 설치" in text
    assert "./scripts/setup_mcp.sh" in text
    assert "[운영 절차의 설치](operations.md#설치)" in text
    assert text.count("setup_mcp.sh") == 1
    assert "--print-config" not in text
    assert "--check" not in text

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


def test_setup_script_dev_check_validates_pytest():
    """--dev --check는 개발 환경의 pytest까지 확인해야 한다."""
    bash = shutil.which("bash")
    assert bash, "bash is required to validate setup_mcp.sh"

    out = subprocess.check_output(
        [bash, str(SCRIPT), "--dev", "--check"],
        text=True,
        cwd=ROOT,
    )

    assert "pytest" in out


def test_pyproject_has_one_dev_dependency_definition_without_unused_plugin():
    """개발 의존성은 하나의 원본으로 pytest만 선언해야 한다."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["optional-dependencies"]["dev"] == [
        "pytest>=9.1.1",
    ]
    assert "dependency-groups" not in project
    assert "pytest-mock" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
