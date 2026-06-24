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
"$PYTHON_BIN" -m venv "$VENV_DIR"

echo "Installing pdf-study into: $VENV_DIR"
"$VENV_PY" -m pip install -U pip setuptools wheel
"$VENV_PY" -m pip install -e "$REPO_DIR"

check_env

cat <<EOF

Copy this MCP config into your MCP client settings.
Use the absolute command path exactly as printed; do not replace it with
"python", "~", or a relative path.

EOF
print_config
