#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup_mcp.sh [--print-config] [--check] [--help]

Create a project-local .venv for pdf-study, install this package into it,
verify required runtime dependencies, and print a copyable MCP config snippet.

Options:
  --print-config   Print the MCP config JSON for this checkout and exit.
  --check          Verify the existing .venv can import required dependencies.
  --help           Show this help.

Environment:
  PYTHON           Python executable used to create the venv (default: python3).
  PDF_STUDY_VENV   Override venv path (default: <repo>/.venv).
EOF
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${PDF_STUDY_VENV:-$REPO_DIR/.venv}"
VENV_PY="$VENV_DIR/bin/python"

print_config() {
  "$PYTHON_BIN" - "$VENV_PY" <<'PY'
from __future__ import annotations

import json
import sys

command = sys.argv[1]
print(json.dumps({
    "mcpServers": {
        "pdf-study": {
            "command": command,
            "args": ["-m", "pdf_study"],
        }
    }
}, ensure_ascii=False, indent=2))
PY
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
    "paddleocr": "paddleocr",
    "pdf_study": "pdf-study",
}

missing = []
for module, package in modules.items():
    try:
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - show exact import failure
        missing.append((package, module, f"{type(exc).__name__}: {exc}"))

if missing:
    print("pdf-study MCP environment check failed:", file=sys.stderr)
    for package, module, error in missing:
        print(f"- {package} ({module}): {error}", file=sys.stderr)
    sys.exit(1)

print(f"pdf-study MCP environment OK: {sys.executable}")
PY
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--print-config" ]]; then
  print_config
  exit 0
fi

if [[ "${1:-}" == "--check" ]]; then
  check_env
  exit 0
fi

if [[ $# -gt 0 ]]; then
  usage >&2
  exit 2
fi

echo "Creating project-local venv: $VENV_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Attempting to install uv automatically for zero-touch setup..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || true
  export PATH="$HOME/.local/bin:$PATH"
fi

if command -v uv >/dev/null 2>&1; then
  echo "uv detected. Forcing uv to download and use Python 3.13 for the local environment..."
  uv venv --python 3.13 "$VENV_DIR"
  uv pip install -e "$REPO_DIR"
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

  echo "Installing pdf-study into: $VENV_DIR"
  "$VENV_PY" -m pip install -U pip setuptools wheel
  "$VENV_PY" -m pip install -e "$REPO_DIR"
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

cat <<EOF

Copy this MCP config into your MCP client settings.
Use the absolute command path exactly as printed; do not replace it with
"python", "~", or a relative path.

EOF
print_config
