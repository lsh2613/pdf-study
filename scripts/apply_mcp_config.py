#!/usr/bin/env python3
"""Apply the pdf-study MCP server entry to client configuration files."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any


TARGETS = ("claude", "codex", "antigravity-cli")
SERVER_CONFIG = {
    "args": ["-m", "pdf_study"],
}


class ConfigError(RuntimeError):
    """Raised when an existing MCP config cannot be safely updated."""


def config_paths(scope: str, project_dir: Path) -> dict[str, Path]:
    if scope == "global":
        return {
            "claude": Path.home() / ".claude.json",
            "codex": Path.home() / ".codex/config/mcp.json",
            "antigravity-cli": Path.home() / ".gemini/antigravity-cli/mcp_config.json",
        }
    if scope == "local":
        return {
            "claude": project_dir / ".claude.json",
            "codex": project_dir / ".codex/mcp.json",
            "antigravity-cli": project_dir / ".agents/mcp_config.json",
        }
    raise ConfigError(f"unknown config scope: {scope}")


def _config_key(target: str, scope: str) -> str:
    return "globalMcpServers" if target == "claude" and scope == "global" else "mcpServers"


def _load_config(path: Path, key: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise ConfigError(f"{path}: expected a regular JSON file")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc.msg})") from exc
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"{path}: cannot read config ({exc})") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level JSON value must be an object")
    if key in data and not isinstance(data[key], dict):
        raise ConfigError(f"{path}: {key} must be an object")
    return data


def _server_entry(command: str, cache_dir: Path) -> dict[str, Any]:
    return {
        **SERVER_CONFIG,
        "command": command,
        "env": {"PDF_STUDY_PADDLEOCR_CACHE": str(cache_dir)},
    }


def _updated_config(
    data: dict[str, Any], key: str, command: str, cache_dir: Path
) -> dict[str, Any]:
    updated = dict(data)
    servers = dict(updated.get(key, {}))
    servers["pdf-study"] = _server_entry(command, cache_dir)
    updated[key] = servers
    return updated


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _snapshot(path: Path) -> tuple[bool, bytes | None, int]:
    if not path.exists():
        return False, None, 0o600
    return True, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _restore(path: Path, snapshot: tuple[bool, bytes | None, int]) -> None:
    existed, content, mode = snapshot
    if existed:
        assert content is not None
        _atomic_write(path, content, mode)
    elif path.exists():
        path.unlink()


def apply_configs(
    *,
    command: str,
    cache_dir: Path,
    project_dir: Path,
    scope: str,
    targets: list[str],
) -> list[Path]:
    paths = config_paths(scope, project_dir)
    unique_targets = list(dict.fromkeys(targets))
    prepared: list[tuple[Path, bytes, int]] = []
    snapshots: dict[Path, tuple[bool, bytes | None, int]] = {}

    # Read and validate every target before creating or changing any file.
    for target in unique_targets:
        path = paths[target]
        key = _config_key(target, scope)
        data = _load_config(path, key)
        snapshots[path] = _snapshot(path)
        updated = _updated_config(data, key, command, cache_dir)
        content = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        prepared.append((path, content, snapshots[path][2]))

    # Keep a recoverable copy before the first replacement.
    for path, _, _ in prepared:
        path.parent.mkdir(parents=True, exist_ok=True)
        if snapshots[path][0]:
            shutil.copy2(path, path.with_name(path.name + ".pdf-study.bak"))

    try:
        for path, content, mode in prepared:
            _atomic_write(path, content, mode)
    except OSError as exc:
        for path, snapshot in snapshots.items():
            try:
                _restore(path, snapshot)
            except OSError as restore_exc:
                raise ConfigError(
                    f"{path}: update failed ({exc}); rollback failed ({restore_exc})"
                ) from restore_exc
        raise ConfigError(f"MCP config update failed ({exc}); changes were rolled back") from exc

    return [path for path, _, _ in prepared]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--scope", required=True, choices=("global", "local"))
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("targets", nargs="+", choices=TARGETS)
    args = parser.parse_args()

    try:
        paths = apply_configs(
            command=args.command,
            cache_dir=args.cache_dir,
            project_dir=args.project_dir,
            scope=args.scope,
            targets=args.targets,
        )
    except (ConfigError, OSError) as exc:
        print(f"❌ Failed to update MCP config: {exc}", file=os.sys.stderr)
        return 1

    for target, path in zip(dict.fromkeys(args.targets), paths):
        print(f"✅ Successfully updated {target} MCP config at: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
