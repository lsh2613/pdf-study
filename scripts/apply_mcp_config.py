#!/usr/bin/env python3
"""Apply the pdf-learner MCP server entry to client configuration files."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any


TARGETS = ("claude", "codex", "antigravity-cli")
SERVER_CONFIG = {
    "args": ["-m", "pdf_learner"],
}


class ConfigError(RuntimeError):
    """Raised when an existing MCP config cannot be safely updated."""


_CODEX_ELICITATION_POLICY = (
    "approval_policy = { granular = { "
    "sandbox_approval = false, "
    "rules = false, "
    "mcp_elicitations = true, "
    "request_permissions = false, "
    "skill_approval = false "
    "} }"
)

_CODEX_ELICITATION_LAUNCH_GUIDANCE = (
    "Launch Codex without --dangerously-bypass-approvals-and-sandbox, --yolo, "
    "or -a never; those CLI flags override config.toml and suppress MCP forms. "
    "For full filesystem access with MCP forms, use: "
    "codex --sandbox danger-full-access"
)


def config_paths(
    scope: str,
    project_dir: Path,
    *,
    home_dir: Path | None = None,
) -> dict[str, Path]:
    if scope != "global":
        raise ConfigError("MCP client configuration is supported only at global scope")
    home = home_dir or Path.home()
    return {
        "claude": home / ".claude.json",
        "antigravity-cli": home / ".gemini/config/mcp_config.json",
    }


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
        "env": {"PDF_LEARNER_PADDLEOCR_CACHE": str(cache_dir)},
    }


def apply_codex_cli_config(
    *,
    command: str,
    cache_dir: Path,
    codex_bin: str,
) -> None:
    """Register and verify the server through Codex CLI's supported interface."""
    add_command = [
        codex_bin,
        "mcp",
        "add",
        "--env",
        f"PDF_LEARNER_PADDLEOCR_CACHE={cache_dir}",
        "pdf-learner",
        "--",
        command,
        "-m",
        "pdf_learner",
    ]
    get_command = [codex_bin, "mcp", "get", "pdf-learner"]
    for cli_command, action in ((add_command, "register"), (get_command, "verify")):
        try:
            completed = subprocess.run(
                cli_command,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise ConfigError(f"Codex CLI {action} failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ConfigError(f"Codex CLI {action} failed: {detail or 'unknown error'}")


def _updated_config(
    data: dict[str, Any], key: str, command: str, cache_dir: Path
) -> dict[str, Any]:
    updated = dict(data)
    servers = dict(updated.get(key, {}))
    servers["pdf-learner"] = _server_entry(command, cache_dir)
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


def ensure_codex_elicitation_allowed(config_path: Path) -> bool:
    """Ensure the global policy allows MCP forms while other prompts fail closed.

    An omitted policy is made explicit because a client or surface default can
    otherwise resolve it to ``never`` for the active thread. Returns ``True``
    when the Codex config was changed. Other interactive policies and an
    already-compatible granular policy are left untouched.
    """
    if not config_path.exists():
        return False
    if not config_path.is_file() or config_path.is_symlink():
        raise ConfigError(f"{config_path}: expected a regular non-symlink TOML file")

    snapshot = _snapshot(config_path)
    assert snapshot[1] is not None
    try:
        text = snapshot[1].decode("utf-8")
        data = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{config_path}: invalid TOML ({exc})") from exc

    policy = data.get("approval_policy")
    if isinstance(policy, str) and policy in {"on-request", "untrusted"}:
        return False
    if isinstance(policy, dict):
        granular = policy.get("granular")
        if (
            isinstance(granular, dict)
            and granular.get("mcp_elicitations") is True
        ):
            return False
        raise ConfigError(
            f"{config_path}: granular approval_policy explicitly does not allow "
            "mcp_elicitations; enable it manually"
        )
    if policy not in {None, "never"}:
        raise ConfigError(
            f"{config_path}: unsupported approval_policy value {policy!r}"
        )

    first_table = re.search(r"(?m)^[ \t]*\[[^\r\n]", text)
    if policy is None:
        insertion = first_table.start() if first_table else len(text)
        prefix = text[:insertion]
        separator = "" if not prefix or prefix.endswith(("\n", "\r")) else "\n"
        updated = (
            prefix
            + separator
            + _CODEX_ELICITATION_POLICY
            + "\n"
            + text[insertion:]
        )
    else:
        root_text = text[:first_table.start()] if first_table else text
        assignment = re.search(
            r"""(?mx)
            ^(?P<indent>[ \t]*)
            approval_policy[ \t]*=[ \t]*
            (?P<quote>["'])never(?P=quote)
            (?P<trailing>[ \t]*(?:\#[^\r\n]*)?)
            (?P<newline>\r?\n|$)
            """,
            root_text,
        )
        if assignment is None:
            raise ConfigError(
                f"{config_path}: could not safely locate the root "
                "approval_policy = \"never\" assignment"
            )
        replacement = (
            f"{assignment.group('indent')}{_CODEX_ELICITATION_POLICY}"
            f"{assignment.group('trailing')}{assignment.group('newline')}"
        )
        updated = text[:assignment.start()] + replacement + text[assignment.end():]
    backup_path = config_path.with_name(config_path.name + ".pdf-learner.bak")
    try:
        shutil.copy2(config_path, backup_path)
        _atomic_write(config_path, updated.encode("utf-8"), snapshot[2])
    except OSError as exc:
        try:
            _restore(config_path, snapshot)
        except OSError as restore_exc:
            raise ConfigError(
                f"{config_path}: approval policy update failed ({exc}); "
                f"rollback failed ({restore_exc})"
            ) from restore_exc
        raise ConfigError(
            f"{config_path}: approval policy update failed ({exc}); "
            "changes were rolled back"
        ) from exc
    return True


def apply_configs(
    *,
    command: str,
    cache_dir: Path,
    project_dir: Path,
    scope: str,
    targets: list[str],
    home_dir: Path | None = None,
) -> list[Path]:
    paths = config_paths(scope, project_dir, home_dir=home_dir)
    unique_targets = list(dict.fromkeys(targets))
    if "codex" in unique_targets:
        raise ConfigError("Codex CLI must be registered through codex mcp, not JSON config")
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
            shutil.copy2(path, path.with_name(path.name + ".pdf-learner.bak"))

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
    parser.add_argument("--scope", required=True, choices=("global",))
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("targets", nargs="+", choices=TARGETS)
    args = parser.parse_args()

    try:
        targets = list(dict.fromkeys(args.targets))
        json_targets = [target for target in targets if target != "codex"]
        paths = apply_configs(
            command=args.command,
            cache_dir=args.cache_dir,
            project_dir=args.project_dir,
            scope=args.scope,
            targets=json_targets,
        ) if json_targets else []

        if "codex" in targets:
            codex_bin = shutil.which("codex")
            if codex_bin is None:
                raise ConfigError("Codex CLI was not found on PATH")
            codex_home = Path(
                os.environ.get("CODEX_HOME", Path.home() / ".codex")
            )
            policy_updated = ensure_codex_elicitation_allowed(
                codex_home / "config.toml"
            )
            apply_codex_cli_config(
                command=args.command,
                cache_dir=args.cache_dir,
                codex_bin=codex_bin,
            )
    except (ConfigError, OSError) as exc:
        print(f"❌ Failed to update MCP config: {exc}", file=os.sys.stderr)
        return 1

    for target, path in zip(json_targets, paths):
        print(f"✅ Successfully updated {target} MCP config at: {path}")
    if "codex" in targets:
        if policy_updated:
            print(
                "✅ Updated Codex approval policy to allow only MCP "
                "elicitation prompts"
            )
        else:
            print(
                "ℹ️ Codex global config does not block MCP elicitation "
                "(CLI, profile, or managed overrides may still apply)"
            )
        print("✅ Successfully registered and verified Codex CLI MCP config")
        print(f"⚠️ {_CODEX_ELICITATION_LAUNCH_GUIDANCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
