#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'INNER_EOF'
Usage: scripts/setup_mcp.sh [--global] [--print-config] [--check] [--dev] [--help]

Create a project-local .venv for pdf-learner, install this package into it,
verify required runtime dependencies, and automatically apply the Codex CLI MCP config.

Options:
  --global         Accepted for compatibility. Codex MCP configuration is always global.

  --print-config   Print the MCP config JSON for this checkout and exit.
  --check          Verify the existing .venv can import required dependencies.
  --dev            Install and verify development dependencies, including pytest.
  --help           Show this help.

Environment:
  PYTHON           Python executable used to create the venv (default: python3).
  PDF_LEARNER_VENV   Override venv path (default: <repo>/.venv).
  PDF_LEARNER_PADDLEOCR_CACHE
                   Override PaddleOCR model cache path (default: <repo>/.paddleocr).
INNER_EOF
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${PDF_LEARNER_VENV:-$REPO_DIR/.venv}"
VENV_PY="$VENV_DIR/bin/python"
PADDLEOCR_CACHE_DIR="${PDF_LEARNER_PADDLEOCR_CACHE:-$REPO_DIR/.paddleocr}"
export PDF_LEARNER_PADDLEOCR_CACHE="$PADDLEOCR_CACHE_DIR"

print_config() {
  "$PYTHON_BIN" - "$VENV_PY" "$PADDLEOCR_CACHE_DIR" <<'PY'
from __future__ import annotations
import json
import sys

command = sys.argv[1]
cache_dir = sys.argv[2]
print(json.dumps({
    "mcpServers": {
        "pdf-learner": {
            "command": command,
            "args": ["-m", "pdf_learner"],
            "env": {
                "PDF_LEARNER_PADDLEOCR_CACHE": cache_dir,
            },
        }
    }
}, ensure_ascii=False, indent=2))
PY
}

apply_config() {
  "$VENV_PY" "$SCRIPT_DIR/apply_mcp_config.py" \
    --command "$VENV_PY" \
    --cache-dir "$PADDLEOCR_CACHE_DIR"
}

check_env() {
  if [[ ! -x "$VENV_PY" ]]; then
    echo "Missing venv Python: $VENV_PY" >&2
    echo "Run scripts/setup_mcp.sh first." >&2
    exit 1
  fi

  "$VENV_PY" - <<'PY'
from __future__ import annotations
import importlib
import sys

modules = {
    "mcp.server.fastmcp": "mcp",
    "fitz": "pymupdf",
    "PIL": "pillow",
    "rich": "rich",
    "markdown_it": "markdown-it-py",
    "paddle": "paddlepaddle",
    "paddleocr": "paddleocr",
    "pdf_learner": "pdf-learner",
}

missing = []
for module, package in modules.items():
    try:
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - show exact import failure
        missing.append((package, module, f"{type(exc).__name__}: {exc}"))

if missing:
    print("pdf-learner MCP environment check failed:", file=sys.stderr)
    for package, module, error in missing:
        print(f"- {package} ({module}): {error}", file=sys.stderr)
    sys.exit(1)

print(f"pdf-learner MCP environment OK: {sys.executable}")
PY
}

check_dev_env() {
  "$VENV_PY" - <<'PY'
from __future__ import annotations
import importlib
import sys

try:
    importlib.import_module("pytest")
except Exception as exc:  # noqa: BLE001 - show exact import failure
    print(f"pdf-learner development environment check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"pdf-learner development environment OK (pytest): {sys.executable}")
PY
}

DEV_MODE=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --help|-h)
      usage
      exit 0
      ;;
    --global)
      shift
      ;;
    --local)
      echo "Project-local MCP configuration is not supported; settings are always global." >&2
      exit 2
      ;;
    --print-config)
      print_config
      exit 0
      ;;
    --check)
      check_env
      if [[ "$DEV_MODE" -eq 1 ]]; then
        check_dev_env
      fi
      exit 0
      ;;
    --dev)
      DEV_MODE=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

INSTALL_SPEC="$REPO_DIR"
if [[ "$DEV_MODE" -eq 1 ]]; then
  INSTALL_SPEC="${REPO_DIR}[dev]"
fi

echo "Creating project-local venv: $VENV_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Attempting to install uv automatically for zero-touch setup..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || true
  export PATH="$HOME/.local/bin:$PATH"
fi

if command -v uv >/dev/null 2>&1; then
  if [[ -x "$VENV_PY" ]]; then
    echo "Reusing existing project-local venv: $VENV_DIR"
  else
    echo "uv detected. Forcing uv to download and use Python 3.13 for the local environment..."
    uv venv --python 3.13 "$VENV_DIR"
  fi
  VIRTUAL_ENV="$VENV_DIR" uv pip install -e "$INSTALL_SPEC"
else
  if [[ -x "$VENV_PY" ]]; then
    echo "Reusing existing project-local venv: $VENV_DIR"
  else
    # Find a compatible Python version (< 3.14) because PaddlePaddle doesn't support 3.14 yet
    for py in python3.13 python3.12 python3.11 python3.10 "$PYTHON_BIN"; do
      if command -v "$py" >/dev/null 2>&1; then
        py_ver=$("$py" -c 'import sys; print(sys.version_info.minor)')
        if [ "$py_ver" -lt 14 ]; then
          PYTHON_BIN="$py"
          break
        fi
      fi
    done

    echo "Using Python binary: $PYTHON_BIN"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  echo "Installing pdf-learner into: $VENV_DIR"
  "$VENV_PY" -m pip install -U pip setuptools wheel
  "$VENV_PY" -m pip install -e "$INSTALL_SPEC"
fi

if [[ "$(uname)" == "Darwin" ]]; then
  if ! brew list libomp >/dev/null 2>&1; then
    echo "macOS detected. PaddleOCR requires OpenMP."
    if command -v brew >/dev/null 2>&1; then
      echo "Installing libomp automatically via Homebrew..."
      brew install libomp
    else
      echo "Warning: Homebrew not found. Please install libomp manually: brew install libomp"
    fi
  else
    echo "libomp is already installed."
  fi
fi

check_env

if [[ "$DEV_MODE" -eq 1 ]]; then
  check_dev_env
fi

echo ""
echo "Applying MCP config to Codex CLI"
apply_config
