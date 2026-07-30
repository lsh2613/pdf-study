"""MCP 전용 venv 설치 스크립트 검증."""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "setup_mcp.sh"


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


def test_runtime_requires_mcp_v1_with_fastmcp_elicitation():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "mcp>=1.28.0,<2" in project["project"]["dependencies"]


def test_setup_script_reuses_existing_project_venv():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'if [[ -x "$VENV_PY" ]]' in text
    assert 'echo "Reusing existing project-local venv: $VENV_DIR"' in text
